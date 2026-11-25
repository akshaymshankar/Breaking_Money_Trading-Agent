"""The trading loop. Same code drives live sessions, historical replay and the demo."""
from __future__ import annotations

import json
import logging
import time as _time
from datetime import datetime, timedelta

from . import compliance, insights, report, skills
from .agents import Desk
from .agents import risk_officer
from .brain import RulesBrain, make_brain
from .clock import parse_hhmm
from .db import Journal
from .engine import PaperBroker
from .feeds.base import PriceFeed
from .indicators import snapshot
from .markets import market_of

log = logging.getLogger("arena")


class Runner:
    def __init__(self, cfg: dict, feed: PriceFeed, journal: Journal, run_id: int, brain=None, report_path=None):
        if feed.live:
            compliance.refuse_live(market_of(cfg))
        self.cfg, self.feed, self.j, self.run_id = cfg, feed, journal, run_id
        self.st = cfg["strategy"]
        self.brain = brain or make_brain(cfg)
        self.brain.on_usage = lambda u: journal.add_usage(run_id, u)
        self.fallback = RulesBrain(cfg)
        self.broker = PaperBroker(journal, run_id, cfg)
        self.cfg.setdefault("skills", list(skills.DEFAULT))
        self.desk = Desk(cfg, feed.live, journal, run_id)
        self.universe = list(dict.fromkeys(cfg["market"]["watchlist"]))
        self.holidays = set(cfg["market"].get("holidays", []))
        self.report_path = report_path or cfg["paths"]["report"]
        self.square_off = parse_hhmm(self.st["square_off_time"])
        self.cutoff = parse_hhmm(self.st["no_new_entries_after"])
        self.day = None
        self.day_start_equity = None
        self.killed = False
        self.next_decision = None
        self.last_snapshot = None
        self.last_report = 0.0
        self.stop_requested = False  # set by the GUI's Stop button
        self.market = market_of(cfg)
        self.phase = "starting"      # shown in the UI: starting | waiting | trading | deciding | paused | complete
        self.phase_detail = ""
        self.last_decision_at = None
        self.decisions_made = 0

    def _sleep(self, seconds: float) -> None:
        """Live: sleep in 1 s slices so Stop takes effect quickly. Replay: advance the clock."""
        if not self.feed.live:
            self.feed.sleep(seconds)
            return
        end = _time.time() + seconds
        while not self.stop_requested and _time.time() < end:
            _time.sleep(min(1.0, end - _time.time()))

    # ---- day lifecycle ----------------------------------------------
    def days_done(self) -> int:
        return self.j.one("SELECT COUNT(*) n FROM days WHERE run_id=?", (self.run_id,))["n"]

    def open_day(self, now: datetime) -> None:
        self.day = now.date()
        self.day_start_equity = self.broker.equity
        self.killed = False
        self.next_decision = now
        log.info("=== Day %d  %s  equity %s ===", self.days_done() + 1, self.day, self.market.money(self.day_start_equity))

    def close_day(self, now: datetime) -> None:
        d = self.day.isoformat()
        if self.st["product"] == "intraday" and self.broker.positions:
            q = self._quotes(list(self.broker.positions))
            for m in self.broker.square_off_all(now, q, "square_off"):
                log.info(m)
        self.broker.snapshot(now)
        day_no = self.days_done() + 1
        stats = self.j.one(
            "SELECT COUNT(*) n, SUM(net_pnl>0) w, SUM(net_pnl<=0) l, COALESCE(SUM(charges),0) c FROM trades "
            "WHERE run_id=? AND substr(exit_ts,1,10)=?", (self.run_id, d))
        ndec = self.j.one("SELECT COUNT(*) n FROM decisions WHERE run_id=? AND substr(ts,1,10)=? AND action!='HOLD'", (self.run_id, d))["n"]
        end_eq = self.broker.equity
        notes = insights.day_insights(self.j, self.run_id, d, self.day_start_equity, end_eq)
        self.j.x("INSERT OR REPLACE INTO days VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (self.run_id, d, day_no, round(self.day_start_equity, 2), round(end_eq, 2),
                  round(end_eq - self.day_start_equity, 2), stats["n"], stats["w"] or 0, stats["l"] or 0,
                  round(stats["c"], 2), ndec, json.dumps(notes)))
        log.info("=== Day %d closed: P&L %s, equity %s ===", day_no, self.market.money(end_eq - self.day_start_equity, True),
                 self.market.money(end_eq))
        self.day = None
        self.write_report()

    # ---- helpers -----------------------------------------------------
    def _quotes(self, symbols: list[str]):
        for attempt in range(5):
            try:
                return self.feed.quotes(symbols)
            except Exception as e:  # keep the session alive through feed hiccups
                log.warning("Quote fetch failed (%s). Retrying...", e)
                if self.feed.live:
                    _time.sleep(min(2 ** attempt, 30))
        return {}

    def write_report(self) -> None:
        try:
            report.write(self.j, self.run_id, self.report_path, live=self.feed.live)
            self.last_report = _time.time()
        except Exception:
            log.exception("Report generation failed")

    def build_context(self, now: datetime, quotes) -> dict:
        market = {}
        for s in self.universe:
            if s in quotes:
                try:
                    bars = self.feed.candles(s, self.st["bar_minutes"], 3)
                except Exception as e:
                    log.warning("Candles for %s failed: %s", s, e)
                    bars = None
                market[s] = snapshot(quotes[s], bars)
        b = self.broker
        positions = [{
            "symbol": p.symbol, "side": p.side, "qty": abs(p.qty), "avg_price": p.avg_price, "ltp": p.last_price,
            "unrealized_pct": round(p.unrealized_pct, 2),
            "stop": p.stop, "target": p.target,
            "held_minutes": round((now - datetime.fromisoformat(p.opened_at)).total_seconds() / 60),
        } for p in b.positions.values()]
        recent = self.j.q("SELECT symbol, side, net_pnl, exit_reason, hold_minutes FROM trades WHERE run_id=? ORDER BY id DESC LIMIT 6", (self.run_id,))
        lessons = [n for r in self.j.q("SELECT insights FROM days WHERE run_id=? ORDER BY date DESC LIMIT 3", (self.run_id,))
                   for n in json.loads(r["insights"] or "[]")[:3]]
        return {
            "market": f"{self.market.name} ({self.market.exchange}), prices in {self.market.currency}",
            "time_local": now.strftime("%Y-%m-%d %H:%M ") + self.market.tz_abbr,
            "challenge_day": f"{self.days_done() + 1} of {self.cfg['account']['challenge_days']}",
            "account": {
                "equity": round(b.equity, 2), "cash": round(b.cash, 2), "buying_power": round(b.buying_power, 2),
                "starting_capital": self.cfg["account"]["starting_capital"],
                "pnl_today": round(b.equity - self.day_start_equity, 2),
                "pnl_total": round(b.equity - self.cfg["account"]["starting_capital"], 2),
                "charges_paid": round(b.total_charges, 2),
            },
            "positions": positions,
            "entries_allowed": now.time() < self.cutoff and not self.killed,
            "snapshot": market,
            "recent_trades": recent,
            "lessons_from_previous_days": lessons,
        }

    def decide_and_execute(self, now: datetime, quotes) -> None:
        ctx = self.build_context(now, quotes)
        brain = self.brain
        self.phase, self.phase_detail = "deciding", "Desk is reading news, mood and regime"
        ctx = self.desk.research(ctx, brain)
        r = ctx["regime"]
        log.info("Desk: %s market (score %+d)%s%s", r["label"], r["score"],
                 f", mood {ctx['intel']['mood_label']} {ctx['intel']['mood_0_fear_100_greed']}/100" if "mood_label" in (ctx.get("intel") or {}) else "",
                 ", shorts open" if r["shorts_allowed"] else "")
        self.phase, self.phase_detail = "deciding", f"Asking {brain.name} for calls"
        log.info("Asking %s for decisions on %d symbols...", brain.name, len(ctx["snapshot"]))
        t0 = _time.time()
        try:
            out = brain.decide(ctx)
            log.info("%s answered in %.0fs: %s", brain.name, _time.time() - t0, (out.get("market_view") or "")[:160])
            self.desk.note("Strategist", "ok", round((_time.time() - t0) * 1000),
                           f"{brain.name}: {sum(d.get('action') != 'HOLD' for d in out.get('decisions', []))} trade calls")
        except Exception as e:
            log.warning("%s failed (%s); using rules fallback for this cycle.", brain.name, e)
            self.desk.note("Strategist", "failed", round((_time.time() - t0) * 1000), f"{brain.name}: {str(e)[:120]}. Rules used instead.")
            brain = self.fallback
            out = brain.decide(ctx)
        self.last_decision_at = now
        self.decisions_made += 1
        self.phase, self.phase_detail = "trading", ""
        view = out.get("market_view", "")
        # Orders reach the market after the decision: re-quote before filling.
        if not self.feed.live:
            self.feed.sleep(self.cfg["execution"]["replay_latency_seconds"])
        exec_time = self.feed.now()
        fresh = self._quotes(self.universe + list(self.broker.positions)) or quotes
        self.broker.mark(fresh)
        # Exits first to free cash, then entries by confidence.
        decs = sorted(out.get("decisions", []), key=lambda d: (d["action"] not in risk_officer.EXITS, -float(d.get("confidence", 0))))
        n_ok = n_no = 0
        for d in decs:
            sym, act = d.get("symbol"), d.get("action", "HOLD")
            cur = self.j.x(
                "INSERT INTO decisions(run_id, ts, symbol, action, allocation_pct, confidence, stop_loss_pct, target_pct, reason, market_view, brain) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, now.isoformat(), sym, act, d.get("allocation_pct", 0), d.get("confidence", 0),
                 d.get("stop_loss_pct", 0), d.get("target_pct", 0), d.get("reason", ""), view, brain.name))
            did, msg = cur.lastrowid, "hold"
            q = fresh.get(sym)
            if act in risk_officer.ENTRIES:
                ok, why = risk_officer.review(d, ctx, self.st, self.market.code)
                if ok is not None and q and self.feed.live and sym not in self.broker.positions:
                    try:
                        checked, extra = skills.check_entry(self.cfg, ok, q.ltp, self.broker.equity, exec_time, self.market.code, self.desk.note)
                    except Exception as e:  # a selected check that breaks must not wave a trade through
                        checked, extra = None, f"skill checks failed: {str(e)[:100]}"
                    ok, why = checked, ", ".join(x for x in (why, extra) if x)
                if not q:
                    msg = "rejected: no quote"
                elif ok is None:
                    msg = f"rejected: {why}"
                else:
                    sl = ok.get("stop_loss_pct") or self.st["default_stop_loss_pct"]
                    tg = ok.get("target_pct") or self.st["default_target_pct"]
                    enter = self.broker.buy if act == "BUY" else self.broker.short
                    filled, msg = enter(exec_time, q, float(ok.get("allocation_pct", 0)), float(sl), float(tg), d.get("reason", ""), did)
                    if filled and why:
                        msg += f" [{why}]"
                    n_ok += filled
                n_no += not msg.startswith(act) or "rejected" in msg
            elif act in risk_officer.EXITS:
                p = self.broker.positions.get(sym)
                if q and p and p.is_short == (act == "COVER"):
                    _, msg = (self.broker.cover if p.is_short else self.broker.sell)(exec_time, q, "ai", d.get("reason", ""), did)
                else:
                    msg = "ignored: not held"
            self.j.x("UPDATE decisions SET outcome=? WHERE id=?", (msg, did))
            if act != "HOLD":
                log.info("[%s] %s %s -> %s", brain.name, act, sym, msg)
        self.desk.note("Risk Officer", "ok", 0, f"{n_ok} entries approved, {n_no} refused")
        self.desk.save(now)

    # ---- main loop ---------------------------------------------------
    def run(self) -> None:
        poll = self.st["poll_seconds"]
        target_days = self.cfg["account"]["challenge_days"]
        while True:
            if self.stop_requested:
                self.broker.snapshot(self.feed.now())
                self.write_report()
                log.info("Paused. Cash, positions and history are saved. Press Start to resume.")
                self.phase, self.phase_detail = "paused", ""
                return
            if self.feed.finished():
                break
            now = self.feed.now()
            if self.day and now.date() != self.day:
                self.close_day(now)
            if not self.market.is_open(now, self.holidays):
                if self.day:
                    self.close_day(now)
                if self.days_done() >= target_days:
                    break
                if self.feed.live:
                    nxt = self.market.next_open(now, self.holidays)
                    wait = (nxt - now).total_seconds()
                    self.phase = "waiting"
                    self.phase_detail = f"{self.market.code} market closed · opens {nxt.strftime('%a %d %b %H:%M')} {self.market.tz_abbr}"
                    log.info("%s market closed. Next open %s %s (in %.0f min). Waiting...", self.market.code,
                             nxt.strftime("%a %d %b %H:%M"), self.market.tz_abbr, wait / 60)
                    self._sleep(min(max(wait, 5), 600))
                else:
                    self._sleep(poll)
                continue
            if self.day is None:
                if self.days_done() >= target_days:
                    break
                self.open_day(now)
            if self.phase != "deciding":
                self.phase, self.phase_detail = "trading", ""
            self.tick(now)
            self._sleep(poll)
        if self.day:
            self.close_day(self.feed.now())
        self.j.x("UPDATE runs SET status='complete', ended_at=? WHERE id=?", (self.feed.now().isoformat(), self.run_id))
        self.phase, self.phase_detail = "complete", ""
        self.write_report()
        log.info("Run %d finished. Report: %s", self.run_id, self.report_path)

    def tick(self, now: datetime) -> None:
        symbols = list(dict.fromkeys(self.universe + list(self.broker.positions)))
        quotes = self._quotes(symbols)
        if not quotes:
            return
        b = self.broker
        b.mark(quotes)
        for m in b.check_exits(now, quotes):
            log.info(m)
        t = now.time()
        max_loss = self.st["max_daily_loss_pct"]
        if not self.killed and b.equity <= self.day_start_equity * (1 - max_loss / 100):
            self.killed = True
            log.warning("Daily loss limit (%.1f%%) hit. Closing positions, no new entries today.", max_loss)
            for m in b.square_off_all(now, quotes, "kill_switch"):
                log.info(m)
        if self.st["product"] == "intraday" and t >= self.square_off:
            for m in b.square_off_all(now, quotes, "square_off"):
                log.info(m)
        elif self.next_decision and now >= self.next_decision:
            self.decide_and_execute(now, quotes)
            self.next_decision = now + timedelta(minutes=self.st["decision_minutes"])
        if not self.last_snapshot or now - self.last_snapshot >= timedelta(minutes=5):
            b.snapshot(now)
            self.last_snapshot = now
        if self.feed.live and _time.time() - self.last_report > self.cfg["report"]["refresh_seconds"]:
            self.write_report()
