"""The trading loop. Same code drives live sessions, historical replay and the demo."""
from __future__ import annotations

import json
import logging
import time as _time
from pathlib import Path
from datetime import datetime, timedelta

from . import insights, report
from .brain import RulesBrain, make_brain
from .clock import CLOSE, is_market_open, next_open, parse_hhmm
from .db import Journal
from .engine import PaperBroker
from .feeds.base import PriceFeed
from .indicators import snapshot

log = logging.getLogger("arena")


class Runner:
    def __init__(self, cfg: dict, feed: PriceFeed, journal: Journal, run_id: int, brain=None, report_path=None):
        self.cfg, self.feed, self.j, self.run_id = cfg, feed, journal, run_id
        self.st = cfg["strategy"]
        self.brain = brain or make_brain(cfg)
        self.fallback = RulesBrain(cfg)
        self.broker = PaperBroker(journal, run_id, cfg)
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
        log.info("=== Day %d  %s  equity Rs %.2f ===", self.days_done() + 1, self.day, self.day_start_equity)

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
        log.info("=== Day %d closed: P&L Rs %+.2f, equity Rs %.2f ===", day_no, end_eq - self.day_start_equity, end_eq)
        self.day = None
        self.write_report()
        try:
            from .daily import save_day
            save_day(self.j, self.run_id, d, Path(self.report_path).parent / "daily")
        except Exception:
            log.exception("Daily report failed")

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
            "symbol": p.symbol, "qty": p.qty, "avg_price": p.avg_price, "ltp": p.last_price,
            "unrealized_pct": round((p.last_price / p.avg_price - 1) * 100, 2) if p.avg_price else 0,
            "stop": p.stop, "target": p.target,
            "held_minutes": round((now - datetime.fromisoformat(p.opened_at)).total_seconds() / 60),
        } for p in b.positions.values()]
        recent = self.j.q("SELECT symbol, net_pnl, exit_reason, hold_minutes FROM trades WHERE run_id=? ORDER BY id DESC LIMIT 6", (self.run_id,))
        lessons = [n for r in self.j.q("SELECT insights FROM days WHERE run_id=? ORDER BY date DESC LIMIT 3", (self.run_id,))
                   for n in json.loads(r["insights"] or "[]")[:3]]
        return {
            "time_ist": now.strftime("%Y-%m-%d %H:%M"),
            "challenge_day": f"{self.days_done() + 1} of {self.cfg['account']['challenge_days']}",
            "account": {
                "equity": round(b.equity, 2), "cash": round(b.cash, 2),
                "starting_capital": self.cfg["account"]["starting_capital"],
                "pnl_today": round(b.equity - self.day_start_equity, 2),
                "pnl_total": round(b.equity - self.cfg["account"]["starting_capital"], 2),
                "charges_paid": round(b.total_charges, 2),
            },
            "positions": positions,
            "entries_allowed": now.time() < self.cutoff and not self.killed,
            "market": market,
            "recent_trades": recent,
            "lessons_from_previous_days": lessons,
        }

    def decide_and_execute(self, now: datetime, quotes) -> None:
        ctx = self.build_context(now, quotes)
        brain = self.brain
        try:
            out = brain.decide(ctx)
        except Exception as e:
            log.warning("%s failed (%s); using rules fallback for this cycle.", brain.name, e)
            brain = self.fallback
            out = brain.decide(ctx)
        view = out.get("market_view", "")
        # Orders reach the market after the decision: re-quote before filling.
        if not self.feed.live:
            self.feed.sleep(self.cfg["execution"]["replay_latency_seconds"])
        exec_time = self.feed.now()
        fresh = self._quotes(self.universe + list(self.broker.positions)) or quotes
        self.broker.mark(fresh)
        # Sells first to free cash, then buys by confidence.
        decs = sorted(out.get("decisions", []), key=lambda d: (d["action"] != "SELL", -float(d.get("confidence", 0))))
        for d in decs:
            sym, act = d.get("symbol"), d.get("action", "HOLD")
            cur = self.j.x(
                "INSERT INTO decisions(run_id, ts, symbol, action, allocation_pct, confidence, stop_loss_pct, target_pct, reason, market_view, brain) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, now.isoformat(), sym, act, d.get("allocation_pct", 0), d.get("confidence", 0),
                 d.get("stop_loss_pct", 0), d.get("target_pct", 0), d.get("reason", ""), view, brain.name))
            did, msg = cur.lastrowid, "hold"
            q = fresh.get(sym)
            if act == "BUY":
                if not q:
                    msg = "rejected: no quote"
                elif not ctx["entries_allowed"]:
                    msg = "rejected: entries closed"
                elif float(d.get("confidence", 0)) < self.st.get("min_confidence", 0):
                    msg = f"rejected: confidence below {self.st['min_confidence']}"
                else:
                    sl = d.get("stop_loss_pct") or self.st["default_stop_loss_pct"]
                    tg = d.get("target_pct") or self.st["default_target_pct"]
                    _, msg = self.broker.buy(exec_time, q, float(d.get("allocation_pct", 0)), float(sl), float(tg), d.get("reason", ""), did)
            elif act == "SELL":
                if q and sym in self.broker.positions:
                    _, msg = self.broker.sell(exec_time, q, "ai", d.get("reason", ""), did)
                else:
                    msg = "ignored: not held"
            self.j.x("UPDATE decisions SET outcome=? WHERE id=?", (msg, did))
            if act != "HOLD":
                log.info("[%s] %s %s -> %s", brain.name, act, sym, msg)

    # ---- main loop ---------------------------------------------------
    def run(self) -> None:
        poll = self.st["poll_seconds"]
        target_days = self.cfg["account"]["challenge_days"]
        while True:
            if self.stop_requested:
                self.broker.snapshot(self.feed.now())
                self.write_report()
                log.info("Paused. Cash, positions and history are saved. Press Start to resume.")
                return
            if self.feed.finished():
                break
            now = self.feed.now()
            if self.day and now.date() != self.day:
                self.close_day(now)
            if not is_market_open(now, self.holidays):
                if self.day:
                    self.close_day(now)
                if self.days_done() >= target_days:
                    break
                if self.feed.live:
                    nxt = next_open(now, self.holidays)
                    wait = (nxt - now).total_seconds()
                    log.info("Market closed. Next open %s (in %.0f min). Waiting...", nxt.strftime("%a %d %b %H:%M"), wait / 60)
                    self._sleep(min(max(wait, 5), 600))
                else:
                    self._sleep(poll)
                continue
            if self.day is None:
                if self.days_done() >= target_days:
                    break
                self.open_day(now)
            self.tick(now)
            self._sleep(poll)
        if self.day:
            self.close_day(self.feed.now())
        self.j.x("UPDATE runs SET status='complete', ended_at=? WHERE id=?", (self.feed.now().isoformat(), self.run_id))
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
