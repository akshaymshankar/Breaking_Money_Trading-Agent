"""Agent fleet: spawn and run several Claude trading agents side by side.

Each agent is an independent paper account (its own run in the journal) with
its own persona, market, watchlist, capital and model, driven by its own
Runner on its own thread. With the Claude Code brain every decision is a
separate `claude -p` call, so a fleet is a set of mini Claude Code agents
competing on the same live prices. Nothing here can place a real order.
"""
from __future__ import annotations

import collections
import json
import logging
import threading
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

from .config import ROOT, load_config
from .db import Journal
from .markets import MARKETS, market_of

log = logging.getLogger("arena")

COLORS = ["#0A84FF", "#30D158", "#FF9F0A", "#BF5AF2", "#FF375F", "#64D2FF", "#FFD60A", "#5E5CE6"]

PRESETS = [
    {
        "key": "momentum", "name": "Momentum Scout", "icon": "bolt", "profile": "balanced", "decision_minutes": 15,
        "persona": "Trend follower. Buy strength: price above VWAP, SMA9 above SMA21, RSI 55-70 and rising volume. "
                   "Ride winners, cut anything that loses VWAP. Prefer the strongest 2-3 names in the watchlist.",
    },
    {
        "key": "reversion", "name": "Mean Reverter", "icon": "arrow.uturn", "profile": "balanced", "decision_minutes": 15,
        "persona": "Contrarian. Look for stretched moves: RSI below 32 after a sharp drop, price well below VWAP but "
                   "stabilising over the last few bars. Small size, quick targets back toward VWAP, tight stops.",
    },
    {
        "key": "breakout", "name": "Breakout Hunter", "icon": "chart.up", "profile": "aggressive", "decision_minutes": 10,
        "persona": "Only trade breakouts above the day's high with volume at least 1.5x average and 30-minute "
                   "momentum above 0.3%. Concentrated, few trades, exit fast if the breakout fails.",
    },
    {
        "key": "guardian", "name": "Risk Guardian", "icon": "shield", "profile": "balanced", "decision_minutes": 20,
        "persona": "Capital preservation first. Trade only A+ setups with confidence above 0.75, at most 2 positions, "
                   "10% allocation each, stops no wider than 1%. HOLD is the default answer.",
    },
    {
        "key": "index", "name": "Index Rider", "icon": "waveform", "profile": "balanced", "decision_minutes": 15,
        "persona": "Follow the broad market. Trade the index ETF and mega caps only in the direction of the index "
                   "trend; stay flat on choppy, range-bound days.",
    },
]


class AgentLog(logging.Handler):
    """Routes log lines to the agent whose thread wrote them (thread name agent-<run_id>)."""

    def __init__(self):
        super().__init__()
        self.all: collections.deque[str] = collections.deque(maxlen=400)
        self.by_agent: dict[int, collections.deque[str]] = collections.defaultdict(lambda: collections.deque(maxlen=200))
        self.names: dict[int, str] = {}

    def emit(self, record):
        try:
            rid = None
            tn = threading.current_thread().name
            if tn.startswith("agent-"):
                rid = int(tn.split("-", 1)[1])
            ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            msg = record.getMessage()
            if record.exc_info and record.levelno >= logging.ERROR:
                msg += " | " + str(record.exc_info[1])[:200]
            who = self.names.get(rid, "system") if rid else "system"
            entry = json.dumps({"t": ts, "a": rid, "who": who, "lvl": record.levelname.lower(), "m": msg})
            self.all.append(entry)
            if rid:
                self.by_agent[rid].append(entry)
        except Exception:
            pass


LOGS = AgentLog()


class Handle:
    def __init__(self, run_id: int):
        self.run_id = run_id
        self.thread: threading.Thread | None = None
        self.runner = None
        self.error = ""
        self.started = 0.0
        self.phase = "starting"
        self.phase_detail = ""

    @property
    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class Fleet:
    def __init__(self, config_path: str | Path | None = None):
        self.config_path = Path(config_path) if config_path else ROOT / "config.toml"
        self.handles: dict[int, Handle] = {}
        self.lock = threading.Lock()

    # ---- config ------------------------------------------------------
    def base_cfg(self, overrides: dict | None = None) -> dict:
        from .__main__ import _brain_cfg
        return _brain_cfg(load_config(self.config_path, overrides), None)

    @property
    def db_path(self) -> str:
        return load_config(self.config_path)["paths"]["db"]

    def journal(self) -> Journal:
        return Journal(self.db_path)

    # ---- spawning ----------------------------------------------------
    def spawn(self, spec: dict, start: bool = True) -> int:
        region = str(spec.get("region") or load_config(self.config_path)["market"]["region"]).upper()
        if region not in MARKETS:
            raise ValueError(f"Unknown market '{region}'")
        preset = next((p for p in PRESETS if p["key"] == spec.get("preset")), None)
        profile = spec.get("profile") or (preset or {}).get("profile")
        overrides = {
            "market": {"region": region},
            "strategy": {"profile": profile} if profile else {},
        }
        cfg = self.base_cfg(overrides)
        st = cfg["strategy"]
        if spec.get("watchlist"):
            wl = spec["watchlist"]
            wl = [w.strip().upper() for w in (wl.split(",") if isinstance(wl, str) else wl) if str(w).strip()]
            if wl:
                cfg["market"]["watchlist"] = wl
        if spec.get("capital"):
            cfg["account"]["starting_capital"] = float(spec["capital"])
        if spec.get("days"):
            cfg["account"]["challenge_days"] = max(1, int(spec["days"]))
        brain = spec.get("brain") or st["brain"]
        if brain == "auto":
            from .brain import find_claude
            import os
            brain = "claude-cli" if find_claude() else "claude-api" if os.environ.get("ANTHROPIC_API_KEY") else "rules"
        st["brain"] = brain
        if spec.get("model") is not None:
            st["cli_model"] = spec["model"] if brain == "claude-cli" else st.get("cli_model", "")
            if brain == "claude-api" and spec["model"]:
                st["model"] = spec["model"]
        if spec.get("decision_minutes") or (preset and preset.get("decision_minutes")):
            st["decision_minutes"] = max(1, int(spec.get("decision_minutes") or preset["decision_minutes"]))
        mode = "replay" if spec.get("mode") == "replay" else "live"
        j = self.journal()
        n_agents = j.one("SELECT COUNT(*) n FROM runs WHERE mode IN ('live','replay')")["n"]
        name = (spec.get("name") or (preset or {}).get("name") or "Claude").strip()
        cfg["agent"] = {
            "name": name,
            "persona": (spec.get("persona") if spec.get("persona") is not None else (preset or {}).get("persona", "")).strip(),
            "preset": (preset or {}).get("key", "custom"),
            "icon": (preset or {}).get("icon", "sparkle"),
            "color": spec.get("color") or COLORS[n_agents % len(COLORS)],
            "primary": bool(spec.get("primary")),
            "mode": mode,
            "replay_days": max(1, min(int(spec.get("replay_days") or 1), 20)),
            "spawned_at": datetime.now().isoformat(timespec="seconds"),
        }
        if mode == "replay":
            cfg["account"]["challenge_days"] = cfg["agent"]["replay_days"]
        mk = market_of(cfg)
        model = st.get("cli_model", "") if st["brain"] == "claude-cli" else st.get("model", "") if st["brain"] == "claude-api" else ""
        rid = j.new_run(name, mode, "yahoo-" + mk.code.lower() if mode == "live" else "history", st["brain"],
                        model, cfg["account"]["starting_capital"], cfg,
                        mk.now().isoformat())
        log.info("Spawned agent #%d '%s' · %s · %s · %s%s", rid, name, mk.code, st["brain"],
                 st.get("cli_model") or "default model", " · replay" if mode == "replay" else "")
        if start:
            self.start(rid)
        return rid

    def primary(self, region: str) -> int:
        """The agent behind the big Start button for this market: resume it, or create it."""
        j = self.journal()
        for r in j.q("SELECT id, config_json FROM runs WHERE status='active' AND mode='live' ORDER BY id DESC"):
            c = json.loads(r["config_json"])
            if market_of(c).code == region and (c.get("agent", {}).get("primary") or "agent" not in c):
                return r["id"]
        return self.spawn({"region": region, "name": f"Claude · {region}", "primary": True,
                           "persona": "", "model": None}, start=False)

    # ---- lifecycle ---------------------------------------------------
    def start(self, run_id: int) -> str:
        with self.lock:
            h = self.handles.get(run_id)
            if h and h.alive:
                return "Already running."
            j = self.journal()
            run = j.one("SELECT * FROM runs WHERE id=?", (run_id,))
            if not run:
                return "No such agent."
            if run["status"] != "active":
                return f"Agent is {run['status']}."
            h = Handle(run_id)
            self.handles[run_id] = h
            LOGS.names[run_id] = run["name"]

            def go():
                try:
                    self._run_agent(h, run)
                except Exception as e:
                    h.error = str(e)[:400]
                    h.phase = "error"
                    log.exception("Agent stopped with an error")
                finally:
                    if h.runner:
                        h.phase, h.phase_detail = h.runner.phase, h.runner.phase_detail
                    if h.phase not in ("error", "complete"):
                        h.phase = "paused"
                    h.runner = None
                    if run["mode"] == "replay":  # a replay cannot resume mid-session: close it out
                        try:
                            self.journal().x("UPDATE runs SET status='complete' WHERE id=? AND status='active'", (run_id,))
                        except Exception:
                            pass

            h.thread = threading.Thread(target=go, name=f"agent-{run_id}", daemon=True)
            h.started = _time.time()
            h.thread.start()
            return "ok"

    def _run_agent(self, h: Handle, run: dict) -> None:
        from . import insights
        from .feeds import make_live_feed
        from .runner import Runner

        cfg = json.loads(run["config_json"])
        cfg.setdefault("market", {}).setdefault("region", "IN")
        mk = market_of(cfg)
        insights.set_currency(mk.symbol)
        cfg["paths"]["db"] = self.db_path
        j = Journal(cfg["paths"]["db"])
        agent = cfg.get("agent", {})
        report = ROOT / "reports" / f"agent_{run['id']}.html"
        if agent.get("primary") or "agent" not in cfg:
            report = Path(load_config(self.config_path)["paths"]["report"])
        if run["mode"] == "replay":
            h.phase, h.phase_detail = "loading", "Downloading recent price history"
            feed = self._replay_feed(cfg, int(agent.get("replay_days", 1)))
            log.info("Replaying %d recent %s session(s) on real prices.", int(agent.get("replay_days", 1)), mk.code)
        else:
            feed = make_live_feed(cfg)
        log.info("%s live · feed %s · brain %s%s · PAPER ONLY", run["name"], feed.name, cfg["strategy"]["brain"],
                 (" (" + cfg["strategy"]["cli_model"] + ")") if cfg["strategy"].get("cli_model") else "")
        h.runner = Runner(cfg, feed, j, run["id"], report_path=report)
        h.runner.write_report()
        h.runner.run()

    def _replay_feed(self, cfg: dict, days: int):
        from .feeds.base import BarReplayFeed
        from .feeds.yahoo import YahooFeed

        mk = market_of(cfg)
        bars = YahooFeed(mk).history(cfg["market"]["watchlist"], period=f"{min(days + 6, 59)}d", interval="5m")
        bars = {s: df for s, df in bars.items() if not df.empty}
        if not bars:
            raise RuntimeError("No price history returned by Yahoo for this watchlist.")
        today = mk.now().date()
        dates = sorted({d for df in bars.values() for d in df.index.date
                        if not (d == today and mk.is_open(mk.now(), set()))})  # skip today's unfinished session
        pick = dates[-days:]
        start = datetime.combine(pick[0], mk.open, tzinfo=mk.tz)
        end = datetime.combine(pick[-1], mk.close, tzinfo=mk.tz)
        bars = {s: df[df.index >= start - timedelta(days=5)] for s, df in bars.items()}  # keep warm-up bars
        return BarReplayFeed(bars, start, end, market=mk)

    def stop(self, run_id: int) -> str:
        h = self.handles.get(run_id)
        if h and h.runner:
            h.runner.stop_requested = True
            h.phase = "stopping"
            return "ok"
        return "Not running."

    def stop_all(self) -> str:
        n = 0
        for rid, h in list(self.handles.items()):
            if h.alive and h.runner:
                h.runner.stop_requested = True
                h.phase = "stopping"
                n += 1
        log.info("Stopping %d agent(s) after their current step...", n)
        return "ok" if n else "Nothing is running."

    def remove(self, run_id: int) -> str:
        self.stop(run_id)
        self.journal().x("UPDATE runs SET status='archived' WHERE id=? AND status!='complete'", (run_id,))
        self.journal().x("UPDATE runs SET mode=mode||'-archived' WHERE id=?", (run_id,))
        log.info("Agent #%d archived (its history stays in the journal).", run_id)
        return "ok"

    @property
    def running(self) -> list[int]:
        return [rid for rid, h in self.handles.items() if h.alive]

    # ---- reporting ---------------------------------------------------
    def summaries(self) -> list[dict]:
        j = self.journal()
        out = []
        rows = j.q("SELECT * FROM runs WHERE mode IN ('live','replay') ORDER BY id DESC LIMIT 40")
        for run in rows:
            out.append(self._summary(j, run))
        return out

    def _summary(self, j: Journal, run: dict) -> dict:
        cfg = json.loads(run["config_json"])
        mk = market_of(cfg)
        agent = cfg.get("agent", {})
        acct = j.one("SELECT * FROM account WHERE run_id=?", (run["id"],)) or {"cash": run["starting_capital"], "charges": 0}
        pos = j.q("SELECT symbol, qty, avg_price, last_price FROM positions WHERE run_id=?", (run["id"],))
        eq = acct["cash"] + sum(p["qty"] * (p["last_price"] or p["avg_price"]) for p in pos)
        tr = j.one("SELECT COUNT(*) n, SUM(net_pnl>0) w FROM trades WHERE run_id=?", (run["id"],))
        ndec = j.one("SELECT COUNT(DISTINCT ts) n FROM decisions WHERE run_id=?", (run["id"],))["n"]
        days = j.one("SELECT COUNT(*) n FROM days WHERE run_id=?", (run["id"],))["n"]
        spark = [r["equity"] for r in j.q(
            "SELECT equity FROM (SELECT ts, equity FROM equity WHERE run_id=? ORDER BY ts DESC LIMIT 80) ORDER BY ts",
            (run["id"],))]
        h = self.handles.get(run["id"])
        alive = bool(h and h.alive)
        phase = (h.runner.phase if h and h.runner else h.phase if h else "") or ""
        detail = (h.runner.phase_detail if h and h.runner else h.phase_detail if h else "") or ""
        if not alive and phase not in ("error",):
            phase = {"complete": "complete", "archived": "archived"}.get(run["status"], "idle")
        cap = run["starting_capital"]
        return {
            "id": run["id"], "name": run["name"], "status": run["status"], "running": alive,
            "phase": phase, "phase_detail": detail, "error": (h.error if h else ""),
            "mode": run["mode"], "region": mk.code, "currency": mk.symbol, "locale": mk.locale,
            "brain": run["brain"],
            "model": (cfg.get("strategy", {}).get("cli_model", "") if run["brain"] == "claude-cli" else run["model"] or ""),
            "persona": agent.get("persona", ""), "preset": agent.get("preset", "custom"),
            "icon": agent.get("icon", "sparkle"), "color": agent.get("color", COLORS[run["id"] % len(COLORS)]),
            "primary": bool(agent.get("primary") or "agent" not in cfg),
            "watchlist": cfg.get("market", {}).get("watchlist", []),
            "decision_minutes": cfg.get("strategy", {}).get("decision_minutes"),
            "capital": cap, "equity": round(eq, 2), "pnl": round(eq - cap, 2),
            "pnl_pct": round((eq / cap - 1) * 100, 3) if cap else 0, "cash": round(acct["cash"], 2),
            "charges": round(acct.get("charges", 0) or 0, 2), "positions": len(pos), "trades": tr["n"],
            "win_rate": round((tr["w"] or 0) / tr["n"] * 100) if tr["n"] else None, "cycles": ndec,
            "days_done": days, "target_days": cfg.get("account", {}).get("challenge_days"),
            "started_at": run["started_at"], "spark": spark,
            "decisions_made": h.runner.decisions_made if h and h.runner else None,
        }

    def detail(self, run_id: int) -> dict:
        j = self.journal()
        run = j.one("SELECT * FROM runs WHERE id=?", (run_id,))
        if not run:
            return {}
        s = self._summary(j, run)
        s["positions_list"] = j.q("SELECT symbol, qty, avg_price, last_price, stop, target, opened_at, entry_reason "
                                  "FROM positions WHERE run_id=? ORDER BY opened_at", (run_id,))
        s["decisions"] = j.q("SELECT id, ts, symbol, action, allocation_pct, confidence, stop_loss_pct, target_pct, reason, "
                             "brain, outcome FROM decisions WHERE run_id=? ORDER BY id DESC LIMIT 80", (run_id,))
        mv = j.one("SELECT ts, market_view, brain FROM decisions WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,))
        s["market_view"] = mv or None
        s["trades_list"] = j.q("SELECT symbol, qty, entry_ts, entry_price, exit_ts, exit_price, net_pnl, charges, "
                               "exit_reason, hold_minutes, entry_reason FROM trades WHERE run_id=? ORDER BY id DESC LIMIT 200",
                               (run_id,))
        s["fills"] = j.q("SELECT ts, symbol, side, qty, fill_price, trigger FROM orders WHERE run_id=? AND status='filled' "
                         "ORDER BY id DESC LIMIT 300", (run_id,))
        s["equity_curve"] = j.q("SELECT ts, equity FROM equity WHERE run_id=? ORDER BY ts", (run_id,))
        s["log"] = [json.loads(x) for x in list(LOGS.by_agent.get(run_id, []))[-80:]]
        return s
