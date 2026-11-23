"""Short selling and the agent desk. Offline: python -m unittest discover tests"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arena import safety  # noqa: E402
from arena.agents import desk as desk_mod  # noqa: E402
from arena.agents import emotion, news_scout, regime, risk_officer  # noqa: E402
from arena.brain import DECISION_SCHEMA, RulesBrain, build_prompt  # noqa: E402
from arena.clock import IST  # noqa: E402
from arena.config import load_config  # noqa: E402
from arena.db import Journal  # noqa: E402
from arena.engine import PaperBroker  # noqa: E402
from arena.feeds.base import BarReplayFeed, Quote  # noqa: E402
from arena.feeds.sim import synth_bars  # noqa: E402
from arena.runner import Runner  # noqa: E402

T = datetime(2026, 10, 5, 10, 0, tzinfo=IST)


def cfg_tmp(tmp, region="IN"):
    cfg = load_config(overrides={"market": {"region": region}})
    cfg["paths"].update(db=f"{tmp}/t.db", report=f"{tmp}/r.html", data_dir=tmp, log=f"{tmp}/l.log")
    cfg["strategy"].update(brain="rules", short_mode="bear_only", product="intraday", profile="balanced")
    return cfg


def q(sym="TEST", ltp=1000.0, vol=0.0):
    return Quote(sym, ltp, ltp, ltp, ltp, ltp, 1e6, T, vol)


class Base(unittest.TestCase):
    region = "IN"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = cfg_tmp(self.tmp, self.region)
        self.st = self.cfg["strategy"]
        self.j = Journal(self.cfg["paths"]["db"])
        self.rid = self.j.new_run("t", "test", "x", "rules", "", 100000, self.cfg, T.isoformat())
        self.b = PaperBroker(self.j, self.rid, self.cfg)

    def tearDown(self):
        self.j.conn.close()


class Shorting(Base):
    def test_round_trip_adds_up(self):
        ok, msg = self.b.short(T, q(), 10, 1, 2, "weak")
        self.assertTrue(ok, msg)
        p = self.b.positions["TEST"]
        self.assertLess(p.qty, 0)
        self.assertGreater(p.stop, p.avg_price)
        self.assertLess(p.target, p.avg_price)
        self.assertLess(p.avg_price, 1000)                       # sold at an adverse price
        self.b.cover(T + timedelta(hours=1), q(ltp=980), "ai", "done")
        tr = self.j.q("SELECT * FROM trades")[0]
        self.assertEqual(tr["side"], "SHORT")
        self.assertGreater(tr["net_pnl"], 0)                     # price fell: a short makes money
        self.assertLess(tr["net_pnl"], 20 * tr["qty"])           # but less than the naive move
        self.assertAlmostEqual(self.b.cash - 100000, tr["net_pnl"], places=1)
        self.assertAlmostEqual(self.b.realized, tr["net_pnl"], places=1)

    def test_loses_when_price_rises(self):
        self.b.short(T, q(), 10, 1, 2, "x")
        self.b.cover(T, q(ltp=1010), "ai")
        self.assertLess(self.b.realized, 0)

    def test_equity_marks_the_right_way(self):
        self.b.short(T, q(), 10, 1, 2, "x")
        e0 = self.b.equity
        self.b.mark({"TEST": q(ltp=990)})
        self.assertGreater(self.b.equity, e0)
        self.assertGreater(self.b.positions["TEST"].unrealized_pct, 0)

    def test_short_proceeds_are_not_spendable(self):
        self.b.short(T, q(), 10, 1, 2, "x")
        self.assertGreater(self.b.cash, 100000)
        self.assertLess(self.b.buying_power, 100000 - 0.9 * self.b.short_value)
        self.st.update(max_position_pct=100)
        self.b.buy(T, q("OTHER", 100), 100, 1, 2, "all in")
        self.assertGreaterEqual(self.b.buying_power, -1)

    def test_limits(self):
        self.b.short(T, q("A"), 90, 1, 2, "x")
        self.assertLessEqual(self.b.short_value, 100000 * 0.10 + 1)     # per-short cap
        self.b.short(T, q("B"), 90, 1, 2, "x")
        ok, msg = self.b.short(T, q("C"), 10, 1, 2, "x")
        self.assertFalse(ok)
        self.assertIn("max open shorts", msg)
        self.assertLessEqual(self.b.short_value, 100000 * 0.20 + 1)

    def test_stop_is_never_wider_than_the_limit(self):
        self.b.short(T, q(), 10, 50, 2, "x")
        p = self.b.positions["TEST"]
        self.assertLessEqual(p.stop / p.avg_price - 1, 0.0201)

    def test_refusals(self):
        for kw, why in (({"short_mode": "off"}, "switched off"), ({"product": "delivery"}, "intraday only")):
            with mock.patch.dict(self.st, kw):
                b = PaperBroker(self.j, self.rid, self.cfg)
                ok, msg = b.short(T, q(), 10, 1, 2, "x")
                self.assertFalse(ok)
                self.assertIn(why, msg)
        self.assertIn("stop", self.b.short(T, q(), 10, 0, 2, "x")[1])
        self.b.buy(T, q(), 10, 1, 2, "long")
        self.assertIn("already", self.b.short(T, q(), 10, 1, 2, "x")[1])

    def test_sell_cannot_close_a_short_nor_cover_a_long(self):
        self.b.short(T, q("S"), 10, 1, 2, "x")
        self.b.buy(T, q("L"), 10, 1, 2, "x")
        self.assertFalse(self.b.sell(T, q("S"), "ai")[0])
        self.assertFalse(self.b.cover(T, q("L"), "ai")[0])
        self.assertEqual(set(self.b.positions), {"S", "L"})

    def test_stop_target_and_square_off(self):
        self.b.short(T, q("A"), 10, 1, 2, "x")
        self.assertIn("stop_loss", self.b.check_exits(T, {"A": q("A", 1015)})[0])
        self.b.short(T, q("B"), 10, 1, 2, "x")
        self.assertIn("target", self.b.check_exits(T, {"B": q("B", 970)})[0])
        self.b.short(T, q("C"), 10, 1, 2, "x")
        self.b.buy(T, q("D"), 10, 1, 2, "x")
        self.b.square_off_all(T, {"C": q("C"), "D": q("D")}, "square_off")
        self.assertEqual(self.b.positions, {})

    def test_margin_call_covers(self):
        self.b.short(T, q(), 10, 1, 2, "x")
        p = self.b.positions["TEST"]
        p.stop = 1e9                                             # pretend the stop could not fire
        self.b.cash = 1000
        msgs = self.b.check_exits(T, {"TEST": q(ltp=1001)})
        self.assertTrue(any("margin_call" in m for m in msgs))
        self.assertEqual(self.b.positions, {})

    def test_borrow_fee_is_charged(self):
        self.b.short(T, q(), 10, 1, 2, "x")
        before = self.b.total_charges
        self.b.cover(T + timedelta(days=30), q(), "ai")
        with_fee = self.b.total_charges - before
        self.st["short_borrow_annual_pct"] = 0.0
        self.b.short(T, q(), 10, 1, 2, "x")
        before = self.b.total_charges
        self.b.cover(T + timedelta(days=30), q(), "ai")
        self.assertGreater(with_fee, self.b.total_charges - before)

    def test_survives_restart_and_old_config(self):
        self.b.short(T, q(), 10, 1, 2, "x")
        old = {**self.cfg, "strategy": {k: v for k, v in self.st.items() if "short" not in k}}
        b2 = PaperBroker(self.j, self.rid, old)
        self.assertTrue(b2.positions["TEST"].is_short)
        self.assertAlmostEqual(b2.equity, self.b.equity, places=1)
        self.assertTrue(b2.cover(T, q(), "ai")[0])

    def test_old_journal_is_migrated(self):
        path = f"{self.tmp}/old.db"
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, run_id INTEGER, symbol TEXT, qty INTEGER, entry_ts TEXT, entry_price REAL, "
                  "exit_ts TEXT, exit_price REAL, gross_pnl REAL, charges REAL, net_pnl REAL, exit_reason TEXT, hold_minutes REAL, entry_reason TEXT)")
        c.execute("INSERT INTO trades(run_id, symbol, qty) VALUES (1,'X',5)")
        c.commit()
        c.close()
        j = Journal(path)
        self.assertEqual(j.one("SELECT side FROM trades")["side"], "LONG")
        j.conn.close()


class Risk(Base):
    region = "US"

    def ctx(self, label="bear", **snap):
        s = {"chg_pct": -2, "rsi14": 38, **snap}
        return {"entries_allowed": True, "positions": [], "snapshot": {"TEST": s}, "intel": {"symbol_tilts": {}},
                "regime": {"label": label, "score": -40, "shorts_allowed": label in ("bear", "panic")}}

    def d(self, **kw):
        return {"symbol": "TEST", "action": "SHORT", "allocation_pct": 10, "confidence": 0.8, "stop_loss_pct": 1, "target_pct": 2, **kw}

    def review(self, d, ctx):
        return risk_officer.review(d, ctx, self.st, "US")

    def test_only_in_bear_markets(self):
        for label, allowed in (("bull", False), ("neutral", False), ("bear", True), ("panic", True)):
            self.assertEqual(self.review(self.d(), self.ctx(label))[0] is not None, allowed, label)

    def test_missing_regime_fails_closed(self):
        ctx = self.ctx()
        del ctx["regime"]
        self.assertIsNone(self.review(self.d(), ctx)[0])

    def test_guards(self):
        c = self.ctx()
        self.assertIn("confidence", self.review(self.d(confidence=0.3), c)[1])
        self.assertIn("confidence", self.review(self.d(confidence=float("nan")), c)[1])
        self.assertIn("Rule 201", self.review(self.d(), self.ctx(chg_pct=-11))[1])
        self.assertIn("squeeze", self.review(self.d(), self.ctx(rsi14=18))[1])
        c["intel"]["symbol_tilts"]["TEST"] = 0.8
        self.assertIn("positive", self.review(self.d(), c)[1])

    def test_shrinks_but_never_grows(self):
        ok, _ = self.review(self.d(allocation_pct=80, stop_loss_pct=9), self.ctx())
        self.assertEqual((ok["allocation_pct"], ok["stop_loss_pct"]), (10, 2.0))
        self.assertEqual(self.review(self.d(), self.ctx("panic"))[0]["allocation_pct"], 5)
        self.assertEqual(self.review(self.d(allocation_pct=3), self.ctx())[0]["allocation_pct"], 3)
        self.assertEqual(self.review(self.d(allocation_pct=-50), self.ctx())[0]["allocation_pct"], 0)

    def test_exits_always_pass(self):
        for act in ("SELL", "COVER"):
            d = self.d(action=act, confidence=0)
            self.assertIsNotNone(risk_officer.review(d, {"entries_allowed": False}, self.st, "US")[0])


class Regime(unittest.TestCase):
    def snap(self, chg, below, down):
        return {f"S{i}": {"chg_pct": chg, "sma9": 1 if down else 2, "sma21": 2 if down else 1, "above_vwap": not below} for i in range(6)}

    def test_labels(self):
        self.assertEqual(regime.assess(self.snap(-1.5, True, True))["label"], "bear")
        self.assertEqual(regime.assess(self.snap(1.5, False, False))["label"], "bull")
        self.assertEqual(regime.assess(self.snap(-3, True, True))["label"], "panic")
        self.assertEqual(regime.assess({})["label"], "neutral")

    def test_one_bad_day_in_an_uptrend_is_not_panic(self):
        g = {"last": 105, "sma20": 100, "sma50": 96, "ret_5d": 3, "vix": 14}
        self.assertIn(regime.assess(self.snap(-0.4, True, False), g)["label"], ("neutral", "bull"))

    def test_gate(self):
        self.assertFalse(regime.shorts_open("bull", "bear_only"))
        self.assertTrue(regime.shorts_open("bear", "bear_only"))
        self.assertTrue(regime.shorts_open("bull", "always"))
        self.assertFalse(regime.shorts_open("bear", "off"))


def rss(*items):
    now = datetime.now(timezone.utc)
    body = "".join(f"<item><title>{t}</title><link>{l}</link><pubDate>{format_datetime(now - timedelta(minutes=m))}</pubDate></item>" for t, l, m in items)
    return f"<rss><channel>{body}</channel></rss>".encode()


class Resp:
    def __init__(self, content, code=200):
        self.content, self.status_code = content, code


class News(unittest.TestCase):
    def test_parse_cleans_and_drops_old(self):
        items = news_scout.parse_feed(rss(("Stocks &amp;lt;b&amp;gt;plunge&amp;lt;/b&amp;gt; on war​ fears", "javascript:alert(1)", 5),
                                          ("A very old headline about markets", "https://x.test/a", 60 * 48)), "S", "market")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Stocks plunge on war fears")
        self.assertEqual(items[0]["link"], "")                       # only http(s) links survive

    def test_entity_bombs_and_huge_feeds_refused(self):
        with self.assertRaises(ValueError):
            news_scout.parse_feed(b'<!DOCTYPE x [<!ENTITY a "b">]><rss/>', "S", "world")
        with self.assertRaises(ValueError):
            news_scout.parse_feed(b"x" * (news_scout.MAX_BYTES + 1), "S", "world")

    def test_tagging(self):
        names = {"AAPL": "Apple", "META": "Meta", "V": "Visa", "ITC": "ITC", "LT": "Larsen & Toubro"}
        self.assertEqual(news_scout.tag_symbols("Apple jumps as iPhone sales beat", names), ["AAPL"])
        self.assertEqual(news_scout.tag_symbols("A V-shaped recovery", names), [])
        self.assertEqual(news_scout.tag_symbols("ITC demerger approved", names), ["ITC"])
        self.assertEqual(news_scout.tag_symbols("Pineapple prices rise", names), [])

    def test_collect_survives_dead_feeds_and_dedupes(self):
        class Http:
            def get(self, url, **kw):
                assert kw["allow_redirects"] is False
                safety.check_url("GET", url)
                if "bbci" in url:
                    return Resp(rss(("Markets rally as truce holds firm", "https://x.test/1", 3)))
                if "npr" in url:
                    return Resp(rss(("Markets rally as truce holds firm", "https://x.test/2", 9)))
                return Resp(b"", 503)
        for region in ("US", "IN"):
            out = news_scout.collect(region, ["AAPL", "M&M"], Http())
            self.assertEqual(len(out["items"]), 1)
            self.assertTrue(out["sources_failed"])

    def test_network_stays_locked(self):
        for bad in ("https://feeds.bbci.co.uk/news/world/other.xml", "https://evil.test/rss.xml", "https://www.cnbc.com/",
                    "http://feeds.npr.org.evil.test/1004/rss.xml"):
            with self.assertRaises(safety.RealMoneyBlocked):
                safety.check_url("GET", bad)
        with self.assertRaises(safety.RealMoneyBlocked):
            safety.check_url("POST", "https://feeds.npr.org/1004/rss.xml")


class FakeAnalyst:
    name = "claude-cli"

    def __init__(self, reply):
        self.reply, self.calls, self.prompt = reply, 0, ""

    def write(self, system, user):
        self.calls += 1
        self.prompt = system + user
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


ITEMS = [{"title": "Stocks crash in panic selloff", "source": "S", "scope": "market", "age_min": 5, "symbols": ["AAPL"]},
         {"title": "War fears as missiles strike", "source": "S", "scope": "world", "age_min": 9, "symbols": []}]


class Emotion(unittest.TestCase):
    def test_words_feel_fear_and_greed(self):
        self.assertEqual(emotion.analyse(ITEMS, {"vix": 35}, ["AAPL"], "x")["dominant"], "fear")
        self.assertLess(emotion.analyse(ITEMS, {"vix": 35}, ["AAPL"], "x")["mood"], 25)
        up = [{"title": "Record rally as stocks soar", "source": "S", "scope": "market", "age_min": 1, "symbols": []}]
        self.assertGreater(emotion.analyse(up, {"vix": 12, "ret_5d": 3}, [], "x")["mood"], 75)

    def test_no_data_is_neutral(self):
        self.assertEqual(emotion.analyse([], {}, [], "x")["mood"], 50)

    def test_claude_reply_is_clamped_and_filtered(self):
        a = FakeAnalyst('sure {"mood": 999, "emotions": {"fear": 7, "joy": 1}, "crowd_read": "<b>calm</b>", "themes": ["bad", {"theme":"t","impact":-9}],'
                        ' "symbol_tilts": {"AAPL": -5, "HACK": 1}, "confidence": "x"}')
        out = emotion.analyse(ITEMS, {}, ["AAPL"], "x", a)
        self.assertLessEqual(out["mood"], 100)
        self.assertEqual(out["symbol_tilts"], {"AAPL": -1.0})
        self.assertEqual(out["emotions"]["fear"], 1)
        self.assertNotIn("joy", out["emotions"])
        self.assertEqual(out["crowd_read"], "calm")
        self.assertIn("untrusted", a.prompt)

    def test_claude_failure_falls_back(self):
        for bad in (RuntimeError("down"), "no json here", '{"mood": null}', "[1]"):
            out = emotion.analyse(ITEMS, {}, [], "x", FakeAnalyst(bad))
            self.assertIn("mood", out)


class DeskFlow(Base):
    region = "US"

    def setUp(self):
        super().setUp()
        desk_mod._cache.clear()
        self.snap = Regime().snap(-1.5, True, True)

    def research(self, live, brain=None, gauges=None, items=ITEMS):
        with mock.patch.object(news_scout, "collect", return_value={"items": items, "sources_ok": 3, "sources_failed": []}) as c, \
             mock.patch.object(regime, "gauges", side_effect=gauges if isinstance(gauges, Exception) else None, return_value=gauges or {}):
            d = desk_mod.Desk(self.cfg, live, self.j, self.rid)
            return d, d.research({"snapshot": self.snap, "positions": []}, brain), c

    def test_replay_never_reads_news(self):
        d, ctx, c = self.research(live=False, brain=FakeAnalyst("{}"))
        c.assert_not_called()
        self.assertNotIn("intel", ctx)
        self.assertEqual(ctx["regime"]["label"], "bear")
        self.assertTrue(ctx["regime"]["shorts_allowed"])

    def test_live_brief_and_shared_cache(self):
        a = FakeAnalyst('{"mood": 20, "confidence": 0.9, "emotions": {"fear": 0.9}}')
        d, ctx, c = self.research(live=True, brain=a)
        self.assertIn("untrusted", ctx["intel"]["note"])
        self.assertEqual(a.calls, 1)
        self.research(live=True, brain=a)
        self.assertEqual(a.calls, 1)                               # second trader reuses the reading
        d.save(T)
        self.assertEqual({r["agent"] for r in self.j.q("SELECT agent FROM desk")},
                         {"News Scout", "Market Gauges", "Emotion Analyst", "Regime Analyst"})

    def test_everything_down_still_answers(self):
        self.snap = {}
        d, ctx, _ = self.research(live=True, gauges=RuntimeError("offline"), items=[])
        self.assertEqual(ctx["regime"]["label"], "neutral")
        self.assertFalse(ctx["regime"]["shorts_allowed"])

    def test_delivery_never_shorts(self):
        self.st["product"] = "delivery"
        _, ctx, _ = self.research(live=False)
        self.assertFalse(ctx["regime"]["shorts_allowed"])
        self.assertIn("Long-only", build_prompt({}, self.cfg)[0])

    def test_prompt_offers_shorts(self):
        s = build_prompt({}, self.cfg)[0]
        self.assertIn("SHORT sells", s)
        self.assertIn("only in a bear or panic market", s)
        self.assertIn("COVER", DECISION_SCHEMA["properties"]["decisions"]["items"]["properties"]["action"]["enum"])


class BearSession(unittest.TestCase):
    """A whole falling day: shorts open, close, and the money adds up."""

    def run_day(self, drift, mode="bear_only", brain=None):
        tmp = tempfile.mkdtemp()
        cfg = cfg_tmp(tmp)
        cfg["strategy"]["short_mode"] = mode
        syms = ["RELIANCE", "TCS", "INFY", "SBIN"]
        cfg["market"]["watchlist"] = syms
        cfg["account"]["challenge_days"] = 1
        bars = synth_bars(syms, [date(2026, 10, 5), date(2026, 10, 6)], seed=3)
        for s, df in bars.items():
            f = 1 + drift * (df.index.hour * 60 + df.index.minute - 555) / 375 * (df.index.date == date(2026, 10, 6))  # day 2 only
            for c in ("open", "high", "low", "close"):
                df[c] = (df[c] * f).round(2)
        start = datetime(2026, 10, 6, 9, 15, tzinfo=IST)
        feed = BarReplayFeed(bars, start, datetime(2026, 10, 6, 15, 30, tzinfo=IST))
        j = Journal(cfg["paths"]["db"])
        rid = j.new_run("bear", "replay", "sim", "rules", "", 100000, cfg, feed.now().isoformat())
        r = Runner(cfg, feed, j, rid, brain=brain)
        r.run()
        return j, rid, r

    def test_bear_day(self):
        j, rid, r = self.run_day(-0.06)
        tr = j.q("SELECT * FROM trades WHERE run_id=?", (rid,))
        shorts = [t for t in tr if t["side"] == "SHORT"]
        self.assertTrue(shorts, "no short in a falling market")
        self.assertEqual(r.broker.positions, {})
        self.assertAlmostEqual(r.broker.cash - 100000, sum(t["net_pnl"] for t in tr), places=0)
        self.assertTrue(j.one("SELECT COUNT(*) n FROM desk WHERE run_id=?", (rid,))["n"])

    def test_bull_day_and_off_switch_have_no_shorts(self):
        for drift, mode in ((0.06, "bear_only"), (-0.06, "off")):
            j, rid, _ = self.run_day(drift, mode)
            self.assertEqual(j.one("SELECT COUNT(*) n FROM orders WHERE side='SHORT' AND status='filled'")["n"], 0)

    def test_reckless_model_is_held_back(self):
        class Reckless:
            name = "claude-api"

            def decide(self, ctx):
                return {"market_view": "", "decisions": [dict(symbol=s, action="SHORT", allocation_pct=500, confidence=1,
                                                              stop_loss_pct=80, target_pct=90, reason="yolo") for s in ctx["snapshot"]]}
        j, rid, r = self.run_day(-0.06, brain=Reckless())
        worst = j.one("SELECT MAX(qty*fill_price) v FROM orders WHERE side='SHORT' AND status='filled'")["v"]
        self.assertLessEqual(worst or 0, 100000 * 0.105)
        self.assertEqual(r.broker.positions, {})
        self.assertGreater(j.one("SELECT COUNT(*) n FROM decisions WHERE outcome LIKE '%rejected%'")["n"], 0)

    def test_rules_brain_covers_when_trend_turns(self):
        s = {"sma9": 2, "sma21": 1, "rsi14": 50, "above_vwap": True, "vwap": 1, "chg_pct": 0, "mom_30m_pct": 0, "atr_pct": 1, "ltp": 1, "day_high": 1}
        out = RulesBrain({"strategy": {"product": "intraday"}}).decide(
            {"positions": [{"symbol": "X", "side": "SHORT"}], "snapshot": {"X": s}, "entries_allowed": True})
        self.assertEqual(out["decisions"][0]["action"], "COVER")


if __name__ == "__main__":
    unittest.main()
