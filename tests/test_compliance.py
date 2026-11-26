"""India prices are more than 30 days old, everywhere. Offline: python -m unittest discover tests"""
from __future__ import annotations

import argparse
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arena import __main__ as cli  # noqa: E402
from arena import compliance, gui, insights, marketdata, report, skills  # noqa: E402
from arena.agents import desk, news_scout, regime  # noqa: E402
from arena.config import load_config  # noqa: E402
from arena.db import Journal  # noqa: E402
from arena.feeds import load_history, make_live_feed  # noqa: E402
from arena.feeds.sim import SimFeed  # noqa: E402
from arena.feeds.yahoo import YahooFeed  # noqa: E402
from arena.fleet import Fleet  # noqa: E402
from arena.markets import IN, US  # noqa: E402
from arena.runner import Runner  # noqa: E402


def bars(mk, days=58, intraday=True):
    """Prices right up to today, so anything that leaks is easy to see. Every calendar day trades."""
    today = mk.now().date()
    if intraday:
        parts = [pd.date_range(datetime.combine(today - timedelta(days=d), mk.open, tzinfo=mk.tz), periods=75, freq="5min")
                 for d in range(days, -1, -1)]
        idx = parts[0].append(parts[1:])
    else:
        idx = pd.date_range(today - timedelta(days=days), today, freq="D", tz=mk.tz)
    n = len(idx)
    return pd.DataFrame({"Open": [100.0 + i % 7 for i in range(n)], "High": [110.0] * n, "Low": [90.0] * n,
                         "Close": [100.0 + i % 5 for i in range(n)], "Volume": [1000] * n}, index=idx)


def fake_yf(mk, asked):
    def days(period):
        return {"d": 1, "mo": 30, "y": 365}[period.lstrip("0123456789")] * int(period.rstrip("dmoy"))

    def get(period, interval):
        asked.append((period, interval))
        return bars(mk, days=min(days(period), 700), intraday=interval != "1d")

    def download(tickers, period="5d", interval="1m", **kw):
        one = get(period, interval)
        return pd.concat({t: one for t in tickers}, axis=1) if len(tickers) > 1 else one

    class Ticker:
        def __init__(self, t):
            pass

        def history(self, period="", interval="", **kw):
            return get(period, interval)
    return types.SimpleNamespace(download=download, Ticker=Ticker)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.asked = []
        marketdata._cache.clear()
        desk._cache.clear()
        self.addCleanup(marketdata._cache.clear)
        self.cut = IN.now().date() - timedelta(days=31)

    def yf(self, mk=IN):
        return mock.patch.dict(sys.modules, {"yfinance": fake_yf(mk, self.asked)})

    def cfg(self, region="IN"):
        c = load_config(overrides={"market": {"region": region}})
        c["paths"].update(db=f"{self.tmp}/t.db", report=f"{self.tmp}/r.html", data_dir=self.tmp, log=f"{self.tmp}/l.log")
        c["strategy"].update(brain="rules")
        return c

    def fleet(self):
        p = mock.patch.object(Fleet, "db_path", property(lambda s: f"{self.tmp}/t.db"))
        p.start()
        self.addCleanup(p.stop)
        return Fleet()

    def run_row(self, rid):
        return Journal(f"{self.tmp}/t.db").one("SELECT * FROM runs WHERE id=?", (rid,))


class Rule(Base):
    def test_thirty_full_days_are_withheld(self):
        now = datetime(2026, 11, 25, 10, 0, tzinfo=IN.tz)
        self.assertEqual(compliance.last_day(IN, now).isoformat(), "2026-10-25")  # 26 Oct to 25 Nov withheld
        self.assertIsNone(compliance.last_day(US, now))

    def test_the_day_is_counted_in_india(self):
        late_us = datetime(2026, 11, 24, 23, 0, tzinfo=US.tz)  # already 25 Nov in India
        self.assertEqual(compliance.last_day(IN, late_us).isoformat(), "2026-10-25")

    def test_unknown_market_counts_as_india(self):
        self.assertTrue(compliance.delayed("XX"))
        self.assertTrue(compliance.delayed(""))
        self.assertFalse(compliance.delayed("us"))

    def test_clip_handles_naive_dates_and_empty(self):
        naive = bars(IN, intraday=False)
        naive.index = naive.index.tz_localize(None)
        self.assertEqual(compliance.clip(naive, IN).index[-1].date(), self.cut)
        self.assertEqual(len(compliance.clip(naive.iloc[:0], IN)), 0)
        self.assertEqual(len(compliance.clip(naive, US)), len(naive))

    def test_it_is_not_a_setting(self):
        self.assertNotIn("lag_days", (ROOT / "config.toml").read_text(encoding="utf-8"))
        for over in ({"market": {"region": "IN", "lag_days": 0}}, {"compliance": {"india_lag_days": 0}}):
            load_config(overrides=over)
            self.assertEqual(compliance.INDIA_LAG_DAYS, 30)


class Doors(Base):
    def test_no_live_feed_for_india(self):
        for feed in ("yahoo", "upstox"):
            c = self.cfg()
            c["market"]["feed"] = feed
            with self.assertRaises(compliance.DelayedMarket):
                make_live_feed(c)

    def test_us_is_still_live(self):
        with self.yf(US):
            self.assertTrue(make_live_feed(self.cfg("US")).live)

    def test_runner_refuses_a_live_indian_feed_from_anywhere(self):
        c = self.cfg()
        j = Journal(c["paths"]["db"])
        rid = j.new_run("t", "live", "x", "rules", "", 100000, c, IN.now().isoformat())
        feed = SimFeed(c["market"]["watchlist"], n_days=1, market=IN)
        feed.live = True
        with self.assertRaises(compliance.DelayedMarket):
            Runner(c, feed, j, rid)
        j.conn.close()

    def test_demo_on_made_up_prices_still_works(self):
        c = self.cfg()
        j = Journal(c["paths"]["db"])
        rid = j.new_run("t", "demo", "x", "rules", "", 100000, c, IN.now().isoformat())
        Runner(c, SimFeed(c["market"]["watchlist"], n_days=1, market=IN), j, rid)
        j.conn.close()

    def test_yahoo_feed_is_clipped(self):
        with self.yf():
            f = YahooFeed(IN)
            hist = f.history(["TCS", "INFY"], period="59d")
            self.assertEqual({df.index[-1].date() for df in hist.values()}, {self.cut})
            for q in f.quotes(["TCS", "INFY"]).values():
                self.assertLessEqual(q.ts.date(), self.cut)
            c = f.candles("TCS")
            self.assertTrue(c.empty or c.index[-1].date() <= self.cut)

    def test_yahoo_feed_us_is_not(self):
        with self.yf(US):
            self.assertEqual(YahooFeed(US).history(["AAPL", "MSFT"])["AAPL"].index[-1].date(), US.now().date())

    def test_upstox_candles_are_clipped_and_quotes_refused(self):
        from arena.feeds.upstox import UpstoxFeed
        f = UpstoxFeed(access_token="x", cache_dir=self.tmp)
        with self.assertRaises(compliance.DelayedMarket):
            f.quotes(["TCS"])
        rows = [[t.isoformat(), 1, 2, 1, 2, 10, 0] for t in bars(IN).index]
        f.http = types.SimpleNamespace(get=lambda *a, **k: types.SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"data": {"candles": rows}}))
        self.assertEqual(f._fetch_candles("u").index[-1].date(), self.cut)

    def test_screen_quotes_and_indices(self):
        with self.yf():
            for d in (marketdata.quotes("IN", ["TCS", "INFY"]), marketdata.quotes("IN", ["TCS"]), marketdata.indices("IN")):
                self.assertTrue(d)
                for q in d.values():
                    self.assertEqual(datetime.fromisoformat(q["ts"]).date(), self.cut)

    def test_every_chart_interval(self):
        with self.yf():
            for i in marketdata.INTERVALS:
                got = [pd.Timestamp(b["time"], unit="s").date() for b in marketdata.candles("IN", "TCS", i)["bars"]]
                self.assertTrue(got, i)
                # chart times are shifted by this computer's own clock offset, so allow one day either side
                self.assertLessEqual(max(got), self.cut + timedelta(days=1), i)
                self.assertGreaterEqual(max(got), self.cut - timedelta(days=1), i)

    def test_us_screen_is_current(self):
        with self.yf(US):
            q = marketdata.quotes("US", ["AAPL", "MSFT"])["AAPL"]
        self.assertEqual(datetime.fromisoformat(q["ts"]).date(), US.now().date())

    def test_indian_symbols_cannot_ride_in_a_us_session(self):
        bad = ["TCS.NS", "RELIANCE.BO", "^NSEI", "^BSESN", "^INDIAVIX", "INE002A01018", "reliance.ns"]
        with self.yf(US):
            self.assertEqual(marketdata.quotes("US", bad), {})
            for s in bad:
                self.assertEqual(marketdata.candles("US", s)["bars"], [], s)
            self.assertEqual(YahooFeed(US).history(bad), {})
            self.assertEqual(YahooFeed(US).quotes(bad), {})
            self.assertEqual(skills._symbols(bad), [])
            self.assertEqual(self.asked, [])
            self.assertEqual(set(marketdata.quotes("US", ["AAPL", "BRK-B", "TCS.NS", "^VIX"])), {"AAPL", "BRK-B", "^VIX"})

    def test_gauges_and_news_mood_refuse_india(self):
        with self.yf():
            with self.assertRaises(compliance.DelayedMarket):
                regime.gauges("IN")
            with self.assertRaises(compliance.DelayedMarket):
                desk.market_intel("IN", ["TCS"])
        self.assertEqual(self.asked, [])

    def test_saved_history_is_clipped(self):
        c = self.cfg()
        (Path(self.tmp) / "history" / "IN").mkdir(parents=True)
        df = bars(IN).rename(columns=str.lower)
        for s in c["market"]["watchlist"]:
            df.to_csv(Path(self.tmp) / "history" / "IN" / f"{s}.csv", index_label="ts")
        self.assertEqual({d.index[-1].date() for d in load_history(c, c["market"]["watchlist"]).values()}, {self.cut})
        self.assertEqual(load_history(self.cfg("US"), ["TCS", "INFY", "RELIANCE"]), {})  # not through the US door

    def test_news_is_not_asked_about_indian_symbols_in_a_us_session(self):
        urls = []

        class Http:
            def get(self, url, **kw):
                urls.append(url)
                return types.SimpleNamespace(status_code=503, content=b"")
        news_scout.collect("US", ["TCS.NS", "^NSEI", "AAPL"], Http())
        mine = [u for u in urls if "feeds.finance.yahoo" in u]
        self.assertTrue(mine and "AAPL" in mine[0] and "NS" not in mine[0].split("region")[0].split("s=")[1], mine)


class OldRecords(Base):
    """Sessions saved before this rule stay hidden until their prices are old enough."""

    def old_run(self, days_ago, region="IN", mode="live"):
        j = Journal(f"{self.tmp}/t.db")
        rid = j.new_run("old", mode, "x", "rules", "", 100000, self.cfg(region), IN.now().isoformat())
        ts = (IN.now() - timedelta(days=days_ago)).isoformat()
        j.x("INSERT INTO equity(run_id, ts, equity) VALUES (?,?,?)", (rid, ts, 100000))
        j.x("INSERT INTO trades(run_id, symbol, qty, entry_ts, entry_price, exit_ts, exit_price, net_pnl, charges, exit_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", (rid, "TCS", 1, ts, 4321.5, ts, 4322.5, 1, 0, "x"))
        return j, rid

    def test_recent_india_is_hidden_everywhere(self):
        for mode in ("live", "replay", "live-archived"):
            j, rid = self.old_run(2, mode=mode)
            d = self.fleet().detail(rid)
            self.assertNotIn("4321.5", str(d))
            self.assertTrue(d["hidden"])
            for fn in (lambda: insights.ai_review(None, j, rid), lambda: report.write(j, rid, f"{self.tmp}/x.html")):
                with self.assertRaises(compliance.DelayedMarket):
                    fn()
            self.assertFalse(Path(f"{self.tmp}/x.html").exists())

    def test_older_india_and_any_us_run_still_show(self):
        for days, region in ((40, "IN"), (2, "US")):
            j, rid = self.old_run(days, region)
            self.assertIn("4321.5", str(self.fleet().detail(rid)))

    def test_us_agent_will_not_take_indian_symbols(self):
        f = self.fleet()
        with self.assertRaises(ValueError):
            f.spawn({"region": "US", "watchlist": "TCS.NS,^NSEI"}, start=False)
        rid = f.spawn({"region": "US", "watchlist": "TCS.NS,AAPL"}, start=False)
        self.assertNotIn("TCS", self.run_row(rid)["config_json"].split('"watchlist": ')[-1].split("]")[0])


class App(Base):
    def test_spawn_turns_live_india_into_a_replay(self):
        rid = self.fleet().spawn({"region": "IN", "mode": "live"}, start=False)
        self.assertEqual(self.run_row(rid)["mode"], "replay")

    def test_spawn_us_stays_live(self):
        rid = self.fleet().spawn({"region": "US", "mode": "live"}, start=False)
        self.assertEqual(self.run_row(rid)["mode"], "live")

    def test_an_old_live_india_agent_cannot_be_restarted(self):
        f = self.fleet()
        rid = Journal(f"{self.tmp}/t.db").new_run("old", "live", "x", "rules", "", 100000, self.cfg(), IN.now().isoformat())
        self.assertIn("30 days", f.start(rid))
        self.assertFalse(f.running)

    def test_start_button_practises(self):
        ctl = gui.Controller.__new__(gui.Controller)
        ctl.config_path, ctl.fleet, ctl.autopilot = ROOT / "config.toml", self.fleet(), mock.Mock()
        with mock.patch.object(Fleet, "start", return_value="ok") as go:
            r = ctl.start("IN")
        self.assertEqual((r["msg"], self.run_row(r["id"])["mode"]), ("ok", "replay"))
        go.assert_called_once_with(r["id"])

    def test_replay_reaches_back_past_the_delay(self):
        with self.yf():
            feed = self.fleet()._replay_feed(self.cfg(), 2)
        self.assertEqual(feed.timeline[-1].date(), self.cut)
        self.assertEqual(len({t.date() for t in feed.timeline}), 2)

    def test_us_replay_still_uses_the_last_session(self):
        c = self.cfg("US")
        with self.yf(US):
            feed = self.fleet()._replay_feed(c, 1)
        self.assertGreaterEqual(feed.timeline[-1].date(), US.now().date() - timedelta(days=1))

    def test_intel_endpoint_says_why(self):
        ctl = gui.Controller.__new__(gui.Controller)
        ctl.config_path = ROOT / "config.toml"
        out = ctl.intel("IN")
        self.assertFalse(out["enabled"])
        self.assertIn("30 days", out["why"])

    def test_command_line(self):
        with self.assertRaises(SystemExit) as e:
            cli.cmd_run(argparse.Namespace(brain="rules", new=True, name=None), self.cfg())
        self.assertIn("30 days", str(e.exception))
        with self.assertRaises(SystemExit):
            cli.cmd_intel(argparse.Namespace(region="IN", claude=False, json=False, headlines=1), self.cfg())


class Wording(unittest.TestCase):
    def test_disclaimer_travels_with_the_app(self):
        def low(p):
            return " ".join((ROOT / p).read_text(encoding="utf-8").lower().split())
        for p in ("arena/web/index.html", "arena/report.py", "README.md", "DISCLAIMER.md", "site/index.html"):
            self.assertTrue("not registered with sebi" in low(p), p)
            self.assertTrue("education" in low(p), p)
        # free software each person runs, never a hosted platform; courses teach, never tip
        for p, needs in (("README.md", ("open source", "own computer", "no hosted service", "sebi-registered broker", "do not include stock tips")),
                         ("DISCLAIMER.md", ("open-source software", "own computer", "hosts no trading platform", "sebi-registered broker",
                                            "do not provide stock tips"))):
            for need in needs:
                self.assertTrue(need in low(p), (p, need))
        for need in ("pr no. 27/2024", "ho/47/17/12(11)2025-mrd-pod3/i/11107/2026", "30 days", "smartodr", "scores"):
            self.assertTrue(need in low("DISCLAIMER.md"), need)


if __name__ == "__main__":
    unittest.main()
