"""Auto-run and the one-copy lock. Offline: python -m unittest discover tests"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arena import always, autopilot, gui  # noqa: E402
from arena.config import load_config  # noqa: E402
from arena.db import Journal  # noqa: E402
from arena.fleet import Fleet  # noqa: E402
from arena.markets import IN, US  # noqa: E402

# Wednesday 25 Nov 2026 is a normal day; Thursday 26 Nov is a US holiday (config.toml); 28 and 29 Nov are a weekend.
def us(d, h, m=0):
    return datetime(2026, 11, d, h, m, tzinfo=US.tz)


def ind(d, h, m=0):
    return datetime(2026, 11, d, h, m, tzinfo=IN.tz)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        text = (ROOT / "config.toml").read_text(encoding="utf-8")
        self.conf = Path(self.tmp) / "config.toml"
        self.conf.write_text(gui.set_toml(text, "autopilot", "enabled", True), encoding="utf-8")
        self.live: set[int] = set()
        self.started: list[int] = []

        def start(f, rid):
            self.started.append(rid)
            self.live.add(rid)
            return "ok"
        for p in (mock.patch.object(Fleet, "db_path", property(lambda s: f"{self.tmp}/t.db")),
                  mock.patch.object(Fleet, "running", property(lambda s: list(self.live))),
                  mock.patch.object(Fleet, "start", start)):
            p.start()
            self.addCleanup(p.stop)
        self.fleet = Fleet(self.conf)
        self.pilot = autopilot.Autopilot(self.fleet)

    def switch(self, **kw):
        t = self.conf.read_text(encoding="utf-8")
        for k, v in kw.items():
            t = gui.set_toml(t, "autopilot", k, v)
        self.conf.write_text(t, encoding="utf-8")

    def run_row(self, rid):
        j = Journal(f"{self.tmp}/t.db")
        r = j.one("SELECT * FROM runs WHERE id=?", (rid,))
        j.conn.close()
        return r


class UsNights(Base):
    def test_starts_at_the_open_and_only_once(self):
        self.assertEqual(len(self.pilot.tick(us(25, 10))), 1)
        self.assertEqual(self.pilot.tick(us(25, 10, 1)), [])
        self.assertEqual(self.run_row(self.started[0])["mode"], "live")

    def test_arms_a_little_before_the_open_not_hours_before(self):
        self.assertEqual(self.pilot.tick(us(25, 7)), [])
        self.assertTrue(self.pilot.tick(us(25, 9, 25)))

    def test_closed_holiday_and_weekend_do_nothing(self):
        for now in (us(25, 20), us(26, 11), us(28, 11), us(29, 11)):
            self.assertEqual(self.pilot.tick(now), [], now)

    def test_starts_again_after_a_crash_but_not_in_a_tight_loop(self):
        self.pilot.tick(us(25, 10))
        self.live.clear()
        self.assertEqual(self.pilot.tick(us(25, 10, 2)), [])
        self.pilot.tried.clear()  # five minutes later
        self.assertTrue(self.pilot.tick(us(25, 10, 8)))
        self.assertEqual(len(set(self.started)), 1)  # the same agent, with its cash and positions

    def test_stop_wins_for_that_session_only(self):
        self.pilot.tick(us(25, 10))
        self.live.clear()
        self.pilot.hold("US", us(25, 11))
        self.pilot.tried.clear()
        self.assertEqual(self.pilot.tick(us(25, 12)), [])
        self.assertEqual(autopilot.Autopilot(self.fleet).tick(us(25, 13)), [])  # also after the app restarts
        self.assertTrue(autopilot.Autopilot(self.fleet).tick(us(27, 10)))

    def test_stop_while_closed_covers_the_coming_session(self):
        self.pilot.hold("US", us(25, 20))
        self.assertEqual(self.pilot.tick(us(27, 10)), [])
        self.assertTrue(self.pilot.tick(datetime(2026, 11, 30, 10, tzinfo=US.tz)))

    def test_start_button_lifts_the_stop(self):
        self.pilot.hold("US", us(25, 11))
        self.pilot.release("US")
        self.assertTrue(self.pilot.tick(us(25, 12)))

    def test_another_agent_running_does_not_block_the_main_one(self):
        rid = self.fleet.spawn({"preset": "momentum", "region": "US"}, start=False)
        self.live.add(rid)
        self.assertTrue(self.pilot.tick(us(25, 10)))


class IndiaDays(Base):
    def test_practises_once_on_an_old_session_and_never_live(self):
        did = self.pilot.tick(ind(25, 10))
        self.assertEqual(len(did), 1)
        row = self.run_row(self.started[0])
        self.assertEqual(row["mode"], "replay")
        self.replayed(self.started[0], self.until(self.started[0]), "2026-11-25")
        self.assertEqual(self.pilot.tick(ind(25, 11)), [])

    def test_the_session_is_more_than_thirty_days_old_and_a_trading_day(self):
        for day in (25, 26, 27):
            d = autopilot.practice_session(ind(day, 10), set())
            self.assertGreater((ind(day, 10).date() - d).days, 30)
            self.assertLess(d.weekday(), 5)

    def test_outside_hours_and_weekends_do_nothing(self):
        for now in (ind(25, 9), ind(25, 16), ind(28, 11)):
            self.assertEqual(self.pilot.tick(now), [], now)

    def replayed(self, rid, day, started):
        j = Journal(f"{self.tmp}/t.db")
        j.x("INSERT INTO equity(run_id, ts, equity) VALUES (?,?,?)", (rid, f"{day}T10:00:00+05:30", 100000))
        j.x("UPDATE runs SET started_at=? WHERE id=?", (f"{started}T09:20:00+05:30", rid))
        j.conn.close()
        self.live.clear()
        self.pilot.tried.clear()

    def until(self, rid):
        return json.loads(self.run_row(rid)["config_json"])["agent"]["replay_until"]

    def test_every_trading_day_gets_a_different_older_session(self):
        got = []
        for d, month_day in ((25, "2026-11-25"), (26, "2026-11-26"), (27, "2026-11-27")):
            did = self.pilot.tick(ind(d, 10))
            self.assertEqual(len(did), 1, d)
            rid = self.started[-1]
            got.append(self.until(rid))
            self.replayed(rid, got[-1], month_day)
            self.assertEqual(self.pilot.tick(ind(d, 11)), [], "once a day")
        self.assertEqual(len(set(got)), 3)

    def test_a_practice_you_started_yourself_counts(self):
        rid = self.fleet.spawn({"region": "IN", "mode": "replay"}, start=False)
        self.replayed(rid, "2026-10-23", "2026-11-25")
        self.assertEqual(self.pilot.tick(ind(25, 10)), [])
        self.assertTrue(self.pilot.tick(ind(26, 10)))
        self.assertNotEqual(self.until(self.started[-1]), "2026-10-23")

    def test_a_failed_download_is_tried_again_but_not_forever(self):
        n = 0
        for minute in range(0, 60, 6):
            n += len(self.pilot.tick(ind(25, 10, minute)))
            self.live.clear()
            self.pilot.tried.clear()
        self.assertEqual(n, autopilot.PRACTICE_TRIES)
        self.assertTrue(self.pilot.tick(ind(26, 10)))

    def test_a_holiday_does_not_make_it_ask_for_the_same_day_forever(self):
        self.pilot.tick(ind(25, 10))
        first = self.until(self.started[-1])
        self.replayed(self.started[-1], "2026-10-20", "2026-11-25")  # the market was shut, an older day was replayed
        self.pilot.tick(ind(25, 11))
        self.pilot.tick(ind(26, 10))
        self.assertNotEqual(self.until(self.started[-1]), first)

    def test_it_can_be_left_out(self):
        self.switch(india_practice=False)
        self.assertEqual(self.pilot.tick(ind(25, 10)), [])

    def test_stop_today(self):
        self.pilot.hold("IN", ind(25, 9, 30))
        self.assertEqual(self.pilot.tick(ind(25, 10)), [])


class Switch(Base):
    def test_off_unless_switched_on(self):
        for c in ({}, {"autopilot": {}}, {"autopilot": {"enabled": "false"}}, {"autopilot": {"enabled": 1}}):
            self.assertFalse(self.pilot.enabled(c))

    def test_off_does_nothing(self):
        self.switch(enabled=False)
        self.assertEqual(self.pilot.tick(us(25, 10)) + self.pilot.tick(ind(25, 10)), [])
        self.assertEqual(self.pilot.status(), {"enabled": False, "note": ""})

    def test_one_market_failing_does_not_stop_the_other(self):
        with mock.patch.object(Fleet, "primary", side_effect=RuntimeError("boom")):
            self.pilot.tick(us(25, 10))
        self.assertIn("problem", self.pilot.note)
        self.assertTrue(self.pilot.tick(ind(25, 10)))

    def test_a_damaged_state_file_waits_instead_of_starting(self):
        self.pilot.state_file.write_text("{oops", encoding="utf-8")
        p = autopilot.Autopilot(self.fleet)
        self.assertEqual(p.tick(us(25, 10)), [])
        self.assertTrue(p.tick(us(27, 10)))

    def test_agents_that_were_running_are_watched_again_after_a_crash(self):
        side = self.fleet.spawn({"preset": "momentum", "region": "US"}, start=False)
        gone = self.fleet.spawn({"preset": "reversion", "region": "US"}, start=False)
        self.live |= {side, gone}
        self.pilot.tick(us(25, 10))
        self.pilot.tick(us(25, 10, 1))
        self.pilot.forget(gone)  # a person stopped this one
        self.live.clear()
        self.started.clear()
        autopilot.Autopilot(self.fleet).tick(us(25, 11))
        self.assertIn(side, self.started)
        self.assertNotIn(gone, self.started)

    def test_archiving_the_running_agent_does_not_start_a_new_account(self):
        ctl = gui.Controller.__new__(gui.Controller)
        ctl.config_path, ctl.fleet, ctl.autopilot = self.conf, self.fleet, self.pilot
        rid = self.fleet.primary("US")
        with mock.patch.object(autopilot.US.__class__, "now", lambda s: us(25, 10).astimezone(s.tz)):
            ctl.remove(rid)
        self.assertEqual(self.pilot.tick(us(25, 10, 5)), [])
        self.assertEqual(self.started, [])

    def test_stop_works_even_with_a_broken_config(self):
        ctl = gui.Controller.__new__(gui.Controller)
        ctl.config_path, ctl.fleet, ctl.autopilot = self.conf, self.fleet, self.pilot
        self.conf.write_text("[oops", encoding="utf-8")
        with mock.patch.object(Fleet, "stop_all", return_value="ok") as s:
            self.assertEqual(ctl.stop(None), {"msg": "ok"})
        s.assert_called_once()
        self.assertEqual(set(self.pilot.data["hold"]), {"US", "IN"})

    def test_an_older_config_without_the_section_can_be_switched(self):
        t = self.conf.read_text(encoding="utf-8")
        self.conf.write_text(t[:t.index("[autopilot]")] + t[t.index("[skills]"):], encoding="utf-8")
        self.assertFalse(self.pilot.enabled())
        ctl = gui.Controller.__new__(gui.Controller)
        ctl.config_path, ctl.fleet, ctl.autopilot = self.conf, self.fleet, self.pilot
        ctl.set_autopilot(True)
        self.assertTrue(self.pilot.enabled())

    def test_buttons_reach_it(self):
        ctl = gui.Controller.__new__(gui.Controller)
        ctl.config_path, ctl.fleet, ctl.autopilot = self.conf, self.fleet, self.pilot
        ctl.set_autopilot(False)
        self.assertFalse(self.pilot.enabled())
        ctl.set_autopilot(True)
        self.assertTrue(self.pilot.enabled())
        rid = self.fleet.primary("US")
        with mock.patch.object(Fleet, "stop", return_value="ok"):
            ctl.stop(rid)
        self.assertIn("US", json.loads(self.pilot.state_file.read_text())["hold"])
        ctl.start("US")
        self.assertNotIn("US", json.loads(self.pilot.state_file.read_text())["hold"])

    def test_stopping_a_side_agent_does_not_hold_the_market(self):
        ctl = gui.Controller.__new__(gui.Controller)
        ctl.config_path, ctl.fleet, ctl.autopilot = self.conf, self.fleet, self.pilot
        rid = self.fleet.spawn({"preset": "momentum", "region": "US"}, start=False)
        with mock.patch.object(Fleet, "stop", return_value="ok"):
            ctl.stop(rid)
        self.assertFalse(self.pilot.state_file.exists())


class Unattended(unittest.TestCase):
    """What must hold when nobody is watching."""

    def runner(self, tmp):
        from arena.feeds.sim import SimFeed
        from arena.markets import market_of
        from arena.runner import Runner
        cfg = load_config(ROOT / "config.toml", {"market": {"region": "US"}})
        cfg["paths"].update(db=f"{tmp}/t.db", report=f"{tmp}/r.html", data_dir=tmp, log=f"{tmp}/l.log")
        cfg["strategy"]["brain"] = "rules"
        j = Journal(cfg["paths"]["db"])
        feed = SimFeed(cfg["market"]["watchlist"], n_days=1, market=market_of(cfg))
        rid = j.new_run("t", "live", "sim", "rules", "", 100000, cfg, feed.now().isoformat())
        feed.live = True

        def make():
            return Runner(cfg, feed, j, rid)
        return make, j, rid, feed

    def test_the_daily_loss_limit_survives_a_restart(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            make, j, rid, feed = self.runner(tmp)
            now = feed.now()
            a = make()
            a.open_day(now)
            j.x("INSERT INTO equity(run_id, ts, equity) VALUES (?,?,?)", (rid, now.isoformat(), 100000))
            j.x("UPDATE account SET cash=96000 WHERE run_id=?", (rid,))
            b = make()
            b.open_day(now)
            self.assertEqual(b.day_start_equity, 100000)  # not 96000
            j.x("INSERT INTO trades(run_id, symbol, qty, entry_ts, entry_price, exit_ts, exit_price, net_pnl, charges, exit_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", (rid, "AAPL", 1, now.isoformat(), 1, now.isoformat(), 1, -4000, 0, "kill_switch"))
            c = make()
            c.open_day(now)
            self.assertTrue(c.killed)
            j.conn.close()

    def test_no_new_decisions_on_old_prices_but_stops_still_run(self):
        from datetime import timedelta
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            make, j, rid, feed = self.runner(tmp)
            r = make()
            now = feed.now()
            r.open_day(now)
            with mock.patch.object(r, "decide_and_execute") as d, mock.patch.object(r.broker, "check_exits", return_value=[]) as e:
                r.tick(now)
                d.assert_called_once()
                r.next_decision = now
                with mock.patch.object(r, "_quotes", return_value={k: v for k, v in feed.quotes(r.universe).items()}):
                    r.tick(now + timedelta(minutes=40))
                d.assert_called_once()
                self.assertEqual(e.call_count, 2)
            j.conn.close()

    def test_stop_is_kept_while_an_agent_is_still_loading(self):
        from arena import fleet as fl
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.object(Fleet, "db_path", property(lambda s: f"{tmp}/t.db")):
            import threading
            f, go = Fleet(ROOT / "config.toml"), threading.Event()
            rid = f.spawn({"region": "US", "brain": "rules"}, start=False)
            with mock.patch("arena.feeds.make_live_feed", side_effect=lambda c: go.wait(10)), mock.patch("arena.runner.Runner") as R:
                f.start(rid)
                self.assertEqual(f.stop(rid), "ok")
                go.set()
                f.handles[rid].thread.join(10)
            R.assert_not_called()
            self.assertFalse(f.handles[rid].alive)
            del fl

    def test_the_plan_limits_survive_a_restart(self):
        from arena import brain
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            f = Path(tmp) / "limits.json"
            saved = dict(brain._limits)
            try:
                brain._limits.clear()
                brain.remember_limits(f)
                brain._note_limits({"unifiedWindows": {"five_hour": {"utilization": .95, "resetsAt": 9e9}}, "isUsingOverage": True})
                brain._limits.clear()     # the app closed
                brain.remember_limits(f)
                self.assertEqual(brain._limits["five_hour"]["used"], 95)
                self.assertIn("overage", brain._limits)
            finally:
                brain._limits_file = None
                brain._limits.clear()
                brain._limits.update(saved)


class OneCopy(unittest.TestCase):
    def test_second_copy_is_refused_until_the_first_ends(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            code = ("import sys,time;sys.path.insert(0,r'%s');from arena.always import OneCopy;"
                    "print(OneCopy(r'%s').take(),flush=True);time.sleep(30)" % (ROOT, tmp))
            p = subprocess.Popen([sys.executable, "-B", "-c", code.replace("print(OneCopy", "o=OneCopy").replace(".take(),flush=True)", ";print(o.take(),flush=True)")],
                                 stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(p.stdout.readline().strip(), "True")
                self.assertFalse(always.OneCopy(tmp).take())
            finally:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True) if os.name == "nt" else p.kill()
                p.wait()
                p.stdout.close()
            mine = always.OneCopy(tmp)
            self.assertTrue(mine.take())  # released by the operating system, even after a kill
            mine.set_port(8799)
            self.assertEqual(always.OneCopy(tmp).port(), 8799)
            mine.drop()

    def test_the_app_and_the_command_line_both_use_it(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cfg = load_config(ROOT / "config.toml", {"market": {"region": "US"}})
            cfg["paths"].update(data_dir=tmp, db=f"{tmp}/t.db")
            mine = always.OneCopy(tmp)
            mine.take()
            mine.set_port(8799)
            opened = []
            with mock.patch.object(gui, "load_config", lambda *a, **k: cfg), mock.patch.object(gui.webbrowser, "open", opened.append), \
                    mock.patch.object(gui, "Controller", side_effect=AssertionError("must not open")):
                with self.assertRaises(SystemExit) as e:
                    gui.serve(8800, True)
                self.assertEqual(e.exception.code, 0)
                self.assertEqual(opened, ["http://127.0.0.1:8799/"])
                with mock.patch.dict(os.environ, {"ARENA_SUPERVISED": "1"}), self.assertRaises(SystemExit) as e:
                    gui.serve(8800, False)
                self.assertEqual(e.exception.code, always.ALREADY_OPEN)
            import argparse
            from arena import __main__ as cli
            with self.assertRaises(SystemExit) as e:
                cli.cmd_run(argparse.Namespace(brain="rules", new=True, name=None), cfg)
            self.assertIn("already open", str(e.exception))
            mine.drop()


class KeepRunning(unittest.TestCase):
    def go(self, codes):
        waits, opened = [], []

        class P:
            def __init__(self, rc):
                self.rc = rc

            def poll(self):
                return self.rc

        def start():
            opened.append(1)
            return P(codes.pop(0))
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
                mock.patch.object(always, "load_config", lambda *a: {"paths": {"db": f"{tmp}/t.db"}, "autopilot": {"enabled": True}}), \
                mock.patch.object(always, "stay_awake"), mock.patch("builtins.print"):
            rc = always.keep_running(start=start, sleep=waits.append, clock=lambda: 0)
        return rc, len(opened), waits

    def test_opens_again_after_a_crash_and_waits_longer_each_time(self):
        rc, n, waits = self.go([1, 1, 1, 0])
        self.assertEqual((rc, n), (0, 4))
        self.assertEqual(waits, [20, 40, 80])
        self.assertEqual(always.next_wait(99), 300)

    def test_closed_on_purpose_stays_closed(self):
        self.assertEqual(self.go([0]), (0, 1, []))

    def test_waits_for_another_copy(self):
        self.assertEqual(self.go([always.ALREADY_OPEN, 0])[2], [60])

    @unittest.skipUnless(os.name == "nt", "Windows Startup folder")
    def test_shortcut_on_and_off(self):
        if not (ROOT / ".venv/Scripts/python.exe").exists():
            self.skipTest("needs the app's own Python")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            f = Path(tmp) / "Start Up"
            self.assertFalse(always.autostart_state(f))
            always.autostart_on(f)
            self.assertTrue(always.autostart_state(f))
            always.autostart_off(f)
            self.assertFalse(always.autostart_state(f))


if __name__ == "__main__":
    unittest.main()
