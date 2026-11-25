"""Skills a trading agent can switch on and off.

Each agent keeps its own selection (cfg["skills"], a list of keys). A selected
skill is triggered in every decision cycle:

  research  runs before Claude decides; its findings go into Claude's context
  check     runs on each entry Claude calls; it may tighten, shrink or refuse it
  desk      one of the built-in desk agents
  manual    interactive, so a button opens it in Claude Code instead

Third-party tools run as separate, time-limited processes with no API keys in
their environment, in an empty scratch folder, with arguments fixed here. A tool
runs only while its file still matches the reviewed version (sha256 below).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from .config import ROOT

log = logging.getLogger("arena")

SKILL_DIR = ROOT / ".claude" / "skills"

# kind, markets ("" = all), what it does in plain words, and what it needs.
REGISTRY: list[dict] = [
    # ---- built-in desk agents
    {"key": "news-scout", "name": "News Scout", "kind": "desk", "icon": "news",
     "about": "Reads world, market and stock headlines before each decision."},
    {"key": "emotion-analyst", "name": "Emotion Analyst", "kind": "desk", "icon": "heart", "requires": "news-scout",
     "about": "Claude scores the crowd's fear and greed from the headlines. One extra Claude call per 15 minutes."},
    {"key": "short-seller", "name": "Short Seller", "kind": "desk", "icon": "chart.down",
     "about": "Lets this agent short weak stocks when the market is bearish."},
    # ---- research skills
    {"key": "trader-scanner", "name": "Chart Scanner", "kind": "research", "icon": "scan", "markets": "US",
     "tool": "trader-scanner/scan.py", "about": "Finds opening-range breakouts on the watchlist."},
    {"key": "trader-news", "name": "Stock News", "kind": "research", "icon": "news", "markets": "US",
     "tool": "trader-news/news.py", "about": "Breaking headlines per stock, tagged, with big movers flagged."},
    {"key": "trader-entry", "name": "Entry Timer", "kind": "research", "icon": "clock", "markets": "US",
     "tool": "trader-entry/entry.py", "about": "Says whether each stock has triggered, is waiting, or is too late."},
    {"key": "trader-analyst", "name": "Earnings Analyst", "kind": "research", "icon": "doc", "markets": "US",
     "tool": "trader-analyst/facts.py", "about": "Latest reported revenue, margins and EPS from SEC filings."},
    {"key": "trader-pelosi", "name": "Congress Tracker", "kind": "research", "icon": "people", "markets": "US",
     "tool": "trader-pelosi/ptr.py", "about": "Nancy Pelosi's newest stock-trade disclosures."},
    # ---- checks on each entry
    {"key": "trader-exit", "name": "Exit Planner", "kind": "check", "icon": "target", "markets": "US",
     "tool": "trader-exit/exit.py", "about": "Plans the stop and a target of twice the risk. Refuses a bad stop. Buys only."},
    {"key": "trader-timemachine", "name": "Time Machine", "kind": "check", "icon": "history", "markets": "US",
     "tool": "trader-timemachine/timemachine.py", "about": "Replays each trade 2,000 times and refuses it when the odds are poor."},
    {"key": "trader-budget", "name": "Budget Manager", "kind": "check", "icon": "wallet", "markets": "US",
     "tool": "trader-budget/size.py", "about": "Shrinks a buy so the stop costs at most 1% of the account. Buys only."},
    {"key": "trader-risk", "name": "Risk Manager", "kind": "check", "icon": "shield", "markets": "US",
     "tool": "trader-risk/check.py", "about": "A second risk check on each buy. If it blocks, the buy is refused. Buys only."},
    # ---- interactive
    {"key": "world-mood", "name": "World Mood", "kind": "manual", "icon": "heart", "prompt": "/world-mood",
     "about": "Ask Claude for a full report on the news and how people feel."},
    {"key": "short-planner", "name": "Short Planner", "kind": "manual", "icon": "chart.down", "prompt": "/short-seller",
     "about": "Plan a paper short with Claude, step by step."},
    {"key": "trader-alerts", "name": "Price Alerts", "kind": "manual", "icon": "bell", "prompt": "/trader-alerts",
     "about": "Set price and stop alerts with Claude."},
    {"key": "socratic-tutor", "name": "Socratic Tutor", "kind": "manual", "icon": "book", "prompt": "/socratic-tutor teach me short selling",
     "about": "Claude teaches you by asking questions until you master it."},
    {"key": "risk-reviewer", "name": "Risk Reviewer", "kind": "manual", "icon": "shield",
     "prompt": "Use the risk-reviewer agent to review the current code for safety.", "about": "Checks this app's code for safety problems."},
]
BY_KEY = {s["key"]: s for s in REGISTRY}
SELECTABLE = [s["key"] for s in REGISTRY if s["kind"] != "manual"]
DEFAULT = ["news-scout", "emotion-analyst", "short-seller"]   # what agents made before this feature had
ALWAYS_ON = [
    {"name": "Regime Analyst", "about": "Calls bull, neutral, bear or panic from prices."},
    {"name": "Strategist", "about": "Claude makes the calls."},
    {"name": "Risk Officer", "about": "Checks every new trade. This is a safety layer."},
    {"name": "Executor", "about": "Paper fills, stops, fees and the journal."},
]

# Reviewed versions (line endings normalised, so Windows checkouts match too).
PINS: dict[str, str] = {}


def _load_pins() -> None:
    f = Path(__file__).with_name("skill_pins.json")
    if f.exists():
        PINS.update(json.loads(f.read_text(encoding="utf-8")))


_load_pins()


def normalise(keys) -> list[str]:
    """Known, selectable keys in a fixed order, without skills whose requirement is off."""
    want = {k for k in (keys or []) if isinstance(k, str)}
    return [k for k in SELECTABLE if k in want and (not BY_KEY[k].get("requires") or BY_KEY[k]["requires"] in want)]


def selected(cfg: dict) -> list[str]:
    v = cfg.get("skills")
    if isinstance(v, dict):  # the [skills] section of config.toml
        v = v.get("enabled")
    return normalise(DEFAULT if v is None else v)


def is_on(cfg: dict, key: str) -> bool:
    return key in selected(cfg)


def short_mode(cfg: dict) -> str:
    """The settings switch and the skill switch must both allow shorting."""
    from .engine import short_setting
    return short_setting(cfg["strategy"], "short_mode") if is_on(cfg, "short-seller") else "off"


def toggle(current: list[str], key: str, on: bool) -> list[str]:
    if key not in SELECTABLE:
        raise ValueError(f"Unknown skill '{key}'")
    want = set(normalise(current))
    if on:
        want.add(key)
        if BY_KEY[key].get("requires"):
            want.add(BY_KEY[key]["requires"])
    else:
        want.discard(key)
    return normalise(want)


# ---- availability -----------------------------------------------------
def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def sec_contact() -> str:
    """The contact SEC asks automated clients for. Only used if the user wrote one down themselves."""
    v = (os.environ.get("SEC_USER_AGENT") or "").strip()
    if not v:
        try:
            v = str(json.loads((ROOT / "trader" / "config.json").read_text(encoding="utf-8")).get("sec_user_agent", "")).strip()
        except (OSError, ValueError):
            v = ""
    return "" if "you@example.com" in v else v


def unavailable(key: str, region: str | None = None) -> str:
    """Why this skill cannot run right now, or '' when it can."""
    s = BY_KEY[key]
    if region and s.get("markets") and region not in s["markets"]:
        return f"{s['markets']} market only"
    if s.get("tool"):
        f = SKILL_DIR / s["tool"]
        if not f.is_file():
            return "not installed"
        if file_hash(f) != PINS.get(s["tool"]):
            return "file changed since it was reviewed"
    if key == "trader-analyst" and not sec_contact():
        return "needs SEC_USER_AGENT in .env (SEC asks for a contact)"
    return ""


# ---- running a tool ---------------------------------------------------
_ENV_KEEP = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "HOME", "USERPROFILE", "LANG", "SSL_CERT_FILE")


class ToolError(RuntimeError):
    """The tool answered with a refusal or failed."""


def run_tool(key: str, args: list[str], cwd: str, timeout: float = 45) -> dict | list:
    s = BY_KEY[key]
    why = unavailable(key)
    if why:
        raise ToolError(why)
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEEP}
    if key == "trader-analyst":
        env["SEC_USER_AGENT"] = sec_contact()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        r = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(SKILL_DIR / s["tool"]), *args], cwd=cwd, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=flags)
    except subprocess.TimeoutExpired:
        raise ToolError(f"no answer in {timeout:.0f}s") from None
    if len(r.stdout) > 4_000_000:
        raise ToolError("answer too large")
    try:
        return json.loads(r.stdout)
    except ValueError:
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        msg = lines[-1] if lines else f"exit code {r.returncode}"
        raise ToolError(_clean(re.sub(r"^[\w]+\.py:\s*", "", msg), 160)) from None


def _clean(text, n: int = 160) -> str:
    from .agents.news_scout import clean
    return clean(str(text), n)


def _symbols(symbols: list[str]) -> list[str]:
    return [s.upper() for s in symbols if re.fullmatch(r"[A-Za-z]{1,5}(\.[A-Za-z]|-[A-Za-z]{1,2})?", s)][:20]


# ---- research skills --------------------------------------------------
def _scanner(syms, d):
    out = run_tool("trader-scanner", ["orb", "--symbols", ",".join(syms), "--json", "--no-write", "--quiet", "--pause", "0.1"], d)
    hits = [{"symbol": h.get("symbol"), "broke_out_at": h.get("at"), "price_then": h.get("price"), "range_high": h.get("or_high"),
             "range_low": h.get("or_low"), "pct_over": h.get("pct_over")} for h in out.get("hits", [])[:10]]
    return {"opening_range_breakouts": hits}, (f"{len(hits)} breakouts: " + ", ".join(str(h["symbol"]) for h in hits)) if hits else "no breakouts"


def _news(syms, d):
    out = run_tool("trader-news", ["--tickers", *syms, "--no-sec", "--json", "--dry-run", "--hours", "6"], d)
    items = [{"stock": _clean(i.get("ticker", ""), 12), "title": _clean(i.get("title", "")), "tags": [_clean(t, 20) for t in i.get("tags", [])][:4]}
             for i in out.get("items", [])[:12]]
    moving = [{"stock": m.get("ticker"), "pct": m.get("pct")} for m in out.get("moves", []) if m.get("moving")]
    return {"headlines": items, "moving_3pct": moving}, f"{len(items)} headlines, {len(moving)} big movers"


def _entry(syms, d):
    def one(sym):
        try:
            e = run_tool("trader-entry", ["check", sym, "--setup", "orb", "--json"], d)
            return sym, {"status": e.get("status"), "why": _clean(e.get("reason", ""), 90), "level": e.get("level"),
                         "vs_level_pct": (e.get("last") or {}).get("vs_level_pct")}
        except ToolError as err:
            return sym, {"status": "unknown", "why": str(err)}
    with ThreadPoolExecutor(max_workers=4) as ex:
        got = dict(ex.map(one, syms[:12]))
    if got and all(v["status"] == "unknown" for v in got.values()):
        raise ToolError(next(iter(got.values()))["why"])
    n = sum(v["status"] == "triggered" for v in got.values())
    return {"long_entry_by_stock": got}, f"{n} of {len(got)} triggered"


def _analyst(syms, d):
    out = run_tool("trader-analyst", [*syms[:6], "--quarters", "2", "--no-filings", "--json"], d, timeout=60)
    return {"reported": json.loads(json.dumps(out, default=str)[:6000]) if len(json.dumps(out)) <= 6000 else _clean(json.dumps(out), 3000)}, \
        f"earnings facts for {min(len(syms), 6)} stocks"


def _pelosi(syms, d):
    out = run_tool("trader-pelosi", ["index", "--member", "pelosi", "--json", "--limit", "5"], d)
    rows = [{"filed": _clean(r.get("filed", ""), 12), "report": _clean(r.get("type_name", ""), 40)} for r in out[:5]]
    return {"pelosi_filings": rows}, f"latest filed {rows[0]['filed']}" if rows else "no filings"


RESEARCH = {"trader-scanner": _scanner, "trader-news": _news, "trader-entry": _entry, "trader-analyst": _analyst, "trader-pelosi": _pelosi}
TTL = {"trader-scanner": 120, "trader-news": 300, "trader-entry": 60, "trader-analyst": 6 * 3600, "trader-pelosi": 6 * 3600}

_cache: dict = {}
_locks: dict = {}
_guard = threading.Lock()


def research_one(key: str, symbols: list[str], region: str) -> tuple[dict, str]:
    """Shared across the fleet, so ten agents with the same watchlist cost one run."""
    why = unavailable(key, region)
    if why:
        raise ToolError(why)
    syms = _symbols(symbols)
    if not syms:
        raise ToolError("no US tickers on the watchlist")
    ck = (key, tuple(syms))
    with _guard:
        lock = _locks.setdefault(ck, threading.Lock())
    with lock:
        hit = _cache.get(ck)
        if hit and _time.time() - hit[0] < TTL[key]:
            return hit[1]
        with tempfile.TemporaryDirectory(prefix="arena-skill-", ignore_cleanup_errors=True) as d:
            val = RESEARCH[key](syms, d)
        _cache[ck] = (_time.time(), val)
        return val


def research(cfg: dict, symbols: list[str], region: str, note) -> dict:
    """Run every selected research skill side by side. `note(agent, status, ms, summary)` records each one."""
    keys = [k for k in selected(cfg) if k in RESEARCH]
    if not keys:
        return {}

    def go(k):
        t0 = _time.time()
        try:
            data, summary = research_one(k, symbols, region)
            return k, data, "ok", round((_time.time() - t0) * 1000), summary
        except Exception as e:
            skipped = bool(unavailable(k, region))
            return k, None, "skipped" if skipped else "failed", round((_time.time() - t0) * 1000), str(e)[:160]

    out = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        for k, data, status, ms, summary in ex.map(go, keys):
            note(BY_KEY[k]["name"], status, ms, summary)
            if data is not None:
                out[k] = data
    return out


# ---- checks on an entry ----------------------------------------------
MAX_LOSS_PCT = 1.0   # of equity, per trade, used by Budget Manager and Risk Manager
POOR_ODDS_R = -0.05  # Time Machine: refuse at or below this average result (in units of risk)


def _num(v) -> float:
    v = float(v)
    if not math.isfinite(v):
        raise ToolError("not a number")
    return v


def check_entry(cfg: dict, d: dict, price: float, equity: float, now: datetime, region: str, note) -> tuple[dict | None, str]:
    """Run the selected checks on one approved entry. Only ever makes it smaller, tighter or refused."""
    keys = [k for k in selected(cfg) if BY_KEY[k]["kind"] == "check"]
    keys = [k for k in keys if not unavailable(k, region)]
    if not keys:
        return d, ""
    st, out, notes = cfg["strategy"], dict(d), []
    sym, buy = d["symbol"], d["action"] == "BUY"
    if not _symbols([sym]):
        return d, ""
    stop = _num(out.get("stop_loss_pct") or st["default_stop_loss_pct"])
    target = _num(out.get("target_pct") or st["default_target_pct"])

    def run(key, fn):
        t0 = _time.time()
        try:
            refused, text = fn()
            note(BY_KEY[key]["name"], "ok", round((_time.time() - t0) * 1000), f"{sym}: {text}")
            return refused, text, False
        except Exception as e:
            note(BY_KEY[key]["name"], "failed", round((_time.time() - t0) * 1000), f"{sym}: {str(e)[:120]}")
            return False, str(e)[:120], True

    with tempfile.TemporaryDirectory(prefix="arena-skill-", ignore_cleanup_errors=True) as tmp:
        p = lambda n: str(Path(tmp) / n)  # noqa: E731

        def make_plan(target_rule: str) -> dict:
            flat = "15:50"
            return run_tool("trader-exit", ["plan", "--symbol", sym, "--entry", f"{price:.2f}", "--stop", f"pct:{stop:g}",
                                            "--target", target_rule, "--flat-by", flat, "--date", now.date().isoformat(),
                                            "--json", "--write", p("plan.json")], tmp)

        if "trader-exit" in keys and buy:
            def exit_plan():
                nonlocal target
                try:
                    pl = make_plan("2R")
                except ToolError as e:
                    if "stop" in str(e) or "target" in str(e):  # a refusal, not a crash
                        return True, str(e)
                    raise
                target = round((_num(pl["target"]) / price - 1) * 100, 2)
                return False, f"stop {pl['stop']}, target {pl['target']}"
            refused, text, _ = run("trader-exit", exit_plan)
            if refused:
                return None, f"Exit Planner: {text}"
            out.update(stop_loss_pct=stop, target_pct=target)

        if "trader-timemachine" in keys:
            def replay():
                t = now - timedelta(minutes=now.minute % 5 + 5, seconds=now.second)  # the last bar that is surely complete
                if (t.hour, t.minute) < (9, 35):
                    raise ToolError("too early in the session")
                sgn = 1 if buy else -1
                r = run_tool("trader-timemachine", [
                    "run", "--symbol", sym, "--side", "buy" if buy else "sell", "--entry", f"{price:.2f}",
                    "--stop", f"{price * (1 - sgn * stop / 100):.2f}", "--target", f"{price * (1 + sgn * target / 100):.2f}",
                    "--entry-time", t.strftime("%I:%M %p").lstrip("0"), "--setup", "level", "--replays", "2000",
                    "--until", "15:50", "--json"], tmp)
                mc = r["mc"]
                avg = _num(mc["avg_R"])
                return avg <= POOR_ODDS_R, (f"target first {100 * _num(mc['p_target']):.0f}%, stop first {100 * _num(mc['p_stop']):.0f}%, "
                                            f"average {avg:+.2f}R")
            refused, text, failed = run("trader-timemachine", replay)
            if refused:
                return None, f"Time Machine: poor odds ({text})"
            if not failed:
                notes.append(f"Time Machine {text}")

        if buy and {"trader-budget", "trader-risk"} & set(keys):
            rules = {"budget": round(equity, 2), "cash_buffer_pct": 0, "max_position_pct": st["max_position_pct"],
                     "max_order_usd": round(equity * st["max_position_pct"] / 100, 2), "min_order_usd": 1,
                     "max_loss_per_trade_usd": round(equity * MAX_LOSS_PCT / 100, 2),
                     "max_daily_loss_usd": round(equity * st["max_daily_loss_pct"] / 100, 2),
                     "max_trades_per_day": 50, "max_orders_per_day": 100, "min_reward_risk": 1.0, "require_stop": True,
                     "no_entries_before": "09:30", "no_entries_after": st["no_new_entries_after"], "flat_by": "15:50",
                     "fractional": False, "paper": True, "blocked": [], "allow_options": False, "allow_margin": False, "allow_short": False}
            Path(p("config.json")).write_text(json.dumps(rules), encoding="utf-8")
            sized: dict = {}

            def size():
                make_plan(f"price:{price * (1 + target / 100):.2f}")
                r = run_tool("trader-budget", ["--plan", p("plan.json"), "--config", p("config.json"), "--json", "--write", p("orders.json")], tmp)
                z = r["sizing"]
                sized.update(z)
                if z.get("refusal") or not z.get("qty"):
                    return True, _clean(z.get("refusal") or "no size fits the limits", 120)
                return False, f"{z['qty']} shares, bound by {z['bound_by']}, max loss {z['max_loss']:g}"

            refused, text, failed = run("trader-budget", size)
            if "trader-budget" in keys:
                if refused:
                    return None, f"Budget Manager: {text}"
                if not failed:
                    cap = round(_num(sized["cost"]) / equity * 100, 2)
                    if cap < _num(out.get("allocation_pct") or 0):
                        out["allocation_pct"] = cap
                        notes.append(f"Budget Manager cut size to {cap:g}%")
            if "trader-risk" in keys:
                def verdict():
                    r = run_tool("trader-risk", ["--orders", p("orders.json"), "--config", p("config.json"), "--no-write", "--json"], tmp)
                    x = r["results"][0]
                    why = "; ".join(_clean(w, 90) for w in x.get("reasons", [])[:2])
                    return x["verdict"] != "PASS", "PASS" if x["verdict"] == "PASS" else f"BLOCK: {why}"
                refused, text, failed = run("trader-risk", verdict)
                if refused or failed:  # a risk check you asked for must actually pass
                    return None, f"Risk Manager: {text}"
    return out, ", ".join(notes)


# ---- manual skills ----------------------------------------------------
def open_in_claude(key: str) -> str:
    """Open an interactive skill in its own Claude Code window, in this project folder."""
    from .brain import PAID_ENV, find_claude, open_terminal, plan_env
    s = BY_KEY.get(key)
    if not s or s["kind"] != "manual":
        raise ValueError("This skill runs inside an agent. Switch it on instead.")
    exe = find_claude()
    if not exe:
        raise RuntimeError("Claude Code is not installed yet.")
    if os.name == "nt":  # straight to the program: `cmd /k` mangles two quoted arguments
        subprocess.Popen([exe, s["prompt"]], creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=str(ROOT), env=plan_env())
    else:
        import shlex
        # `bash -l` reads the profile again, so drop the paid settings after it.
        drop = "unset " + " ".join(PAID_ENV)
        open_terminal(["bash", "-lc", f"{drop}; cd {shlex.quote(str(ROOT))} && {shlex.quote(exe)} {shlex.quote(s['prompt'])}"])
    return f"{s['name']} opened in a Claude Code window."


def listing(cfg: dict, region: str, last: dict | None = None) -> dict:
    on = set(selected(cfg))
    last = last or {}
    rows = [{"key": s["key"], "name": s["name"], "kind": s["kind"], "about": s["about"], "icon": s["icon"], "on": s["key"] in on,
             "requires": s.get("requires", ""), "blocked": unavailable(s["key"], region) if s["kind"] != "manual" else "",
             "last": last.get(s["name"])} for s in REGISTRY]
    return {"skills": rows, "always_on": ALWAYS_ON, "selected": normalise(on)}
