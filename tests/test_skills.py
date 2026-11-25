"""Skill switches: selection, triggering, safety. Offline: python -m unittest discover tests"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arena import skills  # noqa: E402
from arena.agents import desk as desk_mod  # noqa: E402
from arena.agents import news_scout, regime  # noqa: E402
from arena.brain import build_prompt  # noqa: E402
from arena.config import load_config  # noqa: E402
from arena.db import Journal  # noqa: E402
from arena.feeds.base import Quote  # noqa: E402
from arena.feeds.sim import SimFeed  # noqa: E402
from arena.fleet import Fleet  # noqa: E402
from arena.gui import Controller, make_handler  # noqa: E402
from arena.markets import US  # noqa: E402
from arena.runner import Runner  # noqa: E402

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=US.tz)
ITEMS = [{"title": "Stocks crash in panic selloff", "source": "S", "scope": "market", "age_min": 5, "symbols": []}]


def us_cfg(tmp, keys=None):
    cfg = load_config(overrides={"market": {"region": "US"}})
    cfg["paths"].update(db=f"{tmp}/t.db", report=f"{tmp}/r.html", data_dir=tmp, log=f"{tmp}/l.log")
    cfg["strategy"].update(brain="rules", short_mode="bear_only", product="intraday", profile="balanced", max_position_pct=20)
    if keys is not None:
        cfg["skills"] = keys
    return cfg


class Selection(unittest.TestCase):
    def test_defaults_for_old_agents(self):
        self.assertEqual(skills.selected({}), skills.DEFAULT)
        self.assertEqual(skills.selected({"skills": []}), [])
        self.assertEqual(skills.selected({"skills": {"enabled": ["trader-news"]}}), ["trader-news"])

    def test_junk_is_ignored(self):
        self.assertEqual(skills.normalise(["../../evil", 5, None, "socratic-tutor", "trader-exit", "trader-exit"]), ["trader-exit"])
        with self.assertRaises(ValueError):
            skills.toggle([], "nope", True)
        with self.assertRaises(ValueError):
            skills.toggle([], "socratic-tutor", True)      # interactive: cannot run in the background

    def test_requirements_follow(self):
        self.assertEqual(skills.toggle([], "emotion-analyst", True), ["news-scout", "emotion-analyst"])
        self.assertEqual(skills.toggle(["news-scout", "emotion-analyst"], "news-scout", False), [])

    def test_short_needs_both_switches(self):
        for keys, mode, want in ((["short-seller"], "bear_only", "bear_only"), ([], "always", "off"), (["short-seller"], "off", "off")):
            self.assertEqual(skills.short_mode({"skills": keys, "strategy": {"short_mode": mode}}), want)

    def test_prompt_follows_the_short_switch(self):
        tmp = tempfile.mkdtemp()
        self.assertIn("SHORT sells", build_prompt({}, us_cfg(tmp, ["short-seller"]))[0])
        self.assertIn("Long-only", build_prompt({}, us_cfg(tmp, []))[0])


class ToolSafety(unittest.TestCase):
    def test_all_tools_match_the_reviewed_version(self):
        for s in skills.REGISTRY:
            if s.get("tool"):
                self.assertTrue((skills.SKILL_DIR / s["tool"]).is_file(), s["tool"])
                self.assertNotIn("changed", skills.unavailable(s["key"]), s["tool"])

    def test_hash_ignores_line_endings(self):
        d = Path(tempfile.mkdtemp())
        (d / "a").write_bytes(b"x\ny\n")
        (d / "b").write_bytes(b"x\r\ny\r\n")
        self.assertEqual(skills.file_hash(d / "a"), skills.file_hash(d / "b"))

    def test_changed_tool_is_never_run(self):
        d = Path(tempfile.mkdtemp())
        shutil.copytree(skills.SKILL_DIR / "trader-scanner", d / "trader-scanner")
        (d / "trader-scanner" / "scan.py").write_text("print('{}')")
        with mock.patch.object(skills, "SKILL_DIR", d), mock.patch.object(skills.subprocess, "run") as run:
            self.assertIn("changed", skills.unavailable("trader-scanner"))
            with self.assertRaises(skills.ToolError):
                skills.run_tool("trader-scanner", [], str(d))
            run.assert_not_called()

    def test_tool_gets_no_secrets_and_no_shell(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test", "UPSTOX_ACCESS_TOKEN": "tok", "SEC_USER_AGENT": "x"}), \
             mock.patch.object(skills.subprocess, "run", return_value=mock.Mock(stdout="{}", stderr="", returncode=0)) as run:
            skills.run_tool("trader-scanner", ["a; rm -rf /"], "cwd")
        (cmd,), kw = run.call_args
        self.assertNotIn("shell", kw)
        self.assertIn("a; rm -rf /", cmd)                   # one plain argument
        self.assertFalse({"ANTHROPIC_API_KEY", "UPSTOX_ACCESS_TOKEN", "SEC_USER_AGENT"} & set(kw["env"]))
        self.assertIn("-I", cmd)

    def test_broker_skill_is_not_here(self):
        self.assertFalse((skills.SKILL_DIR / "trader-broker").exists())
        self.assertNotIn("trader-broker", skills.BY_KEY)

    def test_bad_tickers_never_reach_a_tool(self):
        self.assertEqual(skills._symbols(["AAPL", "--offline", "../x", "BRK.B", "A B", "TOOLONG"]), ["AAPL", "BRK.B"])

    def test_us_only_skills_stay_off_in_india(self):
        self.assertIn("US market only", skills.unavailable("trader-news", "IN"))
        seen = []
        out = skills.research({"skills": ["trader-news"]}, ["TCS"], "IN", lambda *a: seen.append(a))
        self.assertEqual(out, {})
        self.assertEqual(seen[0][1], "skipped")

    def test_earnings_needs_the_users_own_contact(self):
        with mock.patch.object(skills, "sec_contact", return_value=""):
            self.assertIn("SEC_USER_AGENT", skills.unavailable("trader-analyst", "US"))


class Research(unittest.TestCase):
    def setUp(self):
        skills._cache.clear()
        desk_mod._cache.clear()
        self.tmp = tempfile.mkdtemp()

    def desk(self, keys, live=True, brain=None):
        ran = []
        fake = {k: (lambda syms, d, k=k: (ran.append(k) or ({"found": k}, "ok"))) for k in skills.RESEARCH}
        with mock.patch.dict(skills.RESEARCH, fake), mock.patch.object(skills, "unavailable", return_value=""), \
             mock.patch.object(news_scout, "collect", return_value={"items": ITEMS, "sources_ok": 1, "sources_failed": []}) as news, \
             mock.patch.object(regime, "gauges", return_value={"vix": 18.0}):
            d = desk_mod.Desk(us_cfg(self.tmp, keys), live)
            ctx = d.research({"snapshot": {}, "positions": []}, brain)
        return ran, ctx, d, news

    def test_only_selected_skills_trigger(self):
        ran, ctx, d, news = self.desk(["trader-scanner", "trader-entry"])
        self.assertEqual(sorted(ran), ["trader-entry", "trader-scanner"])
        self.assertEqual(set(ctx["skills"]) - {"note"}, {"trader-scanner", "trader-entry"})
        news.assert_not_called()
        self.assertNotIn("intel", ctx)
        self.assertIn("Chart Scanner", {t["agent"] for t in d.trace})

    def test_nothing_selected_triggers_nothing(self):
        ran, ctx, d, news = self.desk([])
        self.assertEqual(ran, [])
        news.assert_not_called()
        self.assertNotIn("skills", ctx)
        self.assertEqual(ctx["regime"]["short_mode"], "off")
        self.assertEqual({t["agent"] for t in d.trace}, {"Market Gauges", "Regime Analyst"})

    def test_news_without_emotion_never_asks_claude(self):
        brain = mock.Mock()
        brain.name = "claude-cli"
        _, ctx, _, _ = self.desk(["news-scout"], brain=brain)
        brain.write.assert_not_called()
        self.assertTrue(ctx["intel"]["headlines"])
        self.assertNotIn("mood_label", ctx["intel"])

    def test_replay_triggers_no_skill(self):
        ran, ctx, _, news = self.desk(list(skills.SELECTABLE), live=False)
        self.assertEqual(ran, [])
        news.assert_not_called()

    def test_one_failing_skill_does_not_stop_the_others(self):
        def boom(*a):
            raise RuntimeError("down")
        seen = {}
        with mock.patch.dict(skills.RESEARCH, {"trader-scanner": boom, "trader-news": lambda s, d: ({"x": 1}, "fine")}), \
             mock.patch.object(skills, "unavailable", return_value=""):
            out = skills.research({"skills": ["trader-scanner", "trader-news"]}, ["AAPL"], "US", lambda a, s, m, t: seen.update({a: s}))
        self.assertEqual(seen, {"Chart Scanner": "failed", "Stock News": "ok"})
        self.assertEqual(list(out), ["trader-news"])

    def test_fleet_shares_one_run(self):
        n = []
        with mock.patch.dict(skills.RESEARCH, {"trader-scanner": lambda s, d: (n.append(1) or ({}, "ok"))}), \
             mock.patch.object(skills, "unavailable", return_value=""):
            for _ in range(3):
                skills.research_one("trader-scanner", ["AAPL"], "US")
        self.assertEqual(len(n), 1)


class Checks(unittest.TestCase):
    def check(self, keys, replies, action="BUY", **kw):
        calls = []

        def fake(key, args, cwd, timeout=45):
            calls.append(key)
            r = replies.get(key, {})
            if isinstance(r, Exception):
                raise r
            return r
        d = {"symbol": "AAPL", "action": action, "allocation_pct": 20, "stop_loss_pct": 1.5, "target_pct": 3, **kw}
        with mock.patch.object(skills, "run_tool", fake), mock.patch.object(skills, "unavailable", return_value=""):
            out = skills.check_entry(us_cfg(tempfile.mkdtemp(), keys), d, 100.0, 100000, NOW, "US", lambda *a: None)
        return out, calls

    TM = staticmethod(lambda r: {"mc": {"p_target": .3, "p_stop": .3, "avg_R": r}})
    PLAN = {"stop": 98.5, "target": 103.0}

    def test_nothing_selected_changes_nothing(self):
        (d, why), calls = self.check([], {})
        self.assertEqual((calls, why, d["allocation_pct"]), ([], "", 20))

    def test_time_machine_refuses_poor_odds(self):
        self.assertIsNone(self.check(["trader-timemachine"], {"trader-timemachine": self.TM(-0.3)})[0][0])
        self.assertIsNotNone(self.check(["trader-timemachine"], {"trader-timemachine": self.TM(0.1)})[0][0])

    def test_time_machine_outage_does_not_block(self):
        (d, _), _ = self.check(["trader-timemachine"], {"trader-timemachine": skills.ToolError("no data")})
        self.assertIsNotNone(d)

    def test_exit_planner(self):
        (d, _), _ = self.check(["trader-exit"], {"trader-exit": {"stop": 98.5, "target": 103.0}})
        self.assertEqual(d["target_pct"], 3.0)
        (d, why), _ = self.check(["trader-exit"], {"trader-exit": skills.ToolError("the stop is too tight")})
        self.assertIsNone(d)
        self.assertIn("too tight", why)

    def test_budget_only_shrinks(self):
        r = {"trader-exit": self.PLAN, "trader-budget": {"sizing": {"qty": 5, "cost": 500, "bound_by": "max loss", "max_loss": 7}}}
        self.assertEqual(self.check(["trader-budget"], r)[0][0]["allocation_pct"], 0.5)
        self.assertEqual(self.check(["trader-budget"], r, allocation_pct=0.2)[0][0]["allocation_pct"], 0.2)

    def test_risk_manager_must_pass(self):
        r = {"trader-exit": self.PLAN, "trader-budget": {"sizing": {"qty": 5, "cost": 500, "bound_by": "x", "max_loss": 7}}}
        for risk, ok in (({"results": [{"verdict": "PASS", "reasons": []}]}, True),
                         ({"results": [{"verdict": "BLOCK", "reasons": ["too big"]}]}, False),
                         (skills.ToolError("crash"), False)):
            self.assertEqual(self.check(["trader-risk"], {**r, "trader-risk": risk})[0][0] is not None, ok)

    def test_buy_only_checks_leave_shorts_alone(self):
        (d, _), calls = self.check(["trader-exit", "trader-budget", "trader-risk"], {}, action="SHORT")
        self.assertEqual(calls, [])
        self.assertIsNotNone(d)


class InTheLoop(unittest.TestCase):
    """The running agent really triggers what is selected, and stops when it is switched off."""

    def runner(self, keys):
        tmp = tempfile.mkdtemp()
        cfg = us_cfg(tmp, keys)
        cfg["market"]["watchlist"] = ["AAPL", "MSFT"]
        feed = SimFeed(["AAPL", "MSFT"], n_days=1, market=US)
        feed.sleep(3600 * 2)
        j = Journal(cfg["paths"]["db"])
        rid = j.new_run("s", "live", "sim", "rules", "", 100000, cfg, feed.now().isoformat())

        class Buyer:
            name = "claude-api"

            def decide(self, ctx):
                Buyer.ctx = ctx
                return {"market_view": "", "decisions": [dict(symbol="AAPL", action="BUY", allocation_pct=10, confidence=.9, stop_loss_pct=1, target_pct=2, reason="r")]}
        r = Runner(cfg, feed, j, rid, brain=Buyer())
        r.open_day(feed.now())
        feed.live = True
        r.desk.live = True
        feed.sleep = lambda s: None
        return r, Buyer

    def decide(self, r, check=None):
        with mock.patch.dict(skills.RESEARCH, {"trader-scanner": lambda s, d: ({"hit": 1}, "1 breakout")}), \
             mock.patch.object(skills, "unavailable", return_value=""), mock.patch.object(regime, "gauges", return_value={}), \
             mock.patch.object(skills, "run_tool", check or (lambda *a, **k: {"mc": {"p_target": .1, "p_stop": .8, "avg_R": -.6}})):
            r.decide_and_execute(r.feed.now(), r.feed.quotes(["AAPL", "MSFT"]))

    def setUp(self):
        skills._cache.clear()

    def test_selected_skill_reaches_claude_and_the_journal(self):
        r, buyer = self.runner(["trader-scanner"])
        self.decide(r)
        self.assertEqual(buyer.ctx["skills"]["trader-scanner"], {"hit": 1})
        self.assertIn("Chart Scanner", {x["agent"] for x in r.j.q("SELECT agent FROM desk")})
        self.assertIn("AAPL", r.broker.positions)

    def test_selected_check_refuses_the_trade(self):
        r, _ = self.runner(["trader-timemachine"])
        self.decide(r)
        self.assertEqual(r.broker.positions, {})
        self.assertIn("Time Machine", r.j.one("SELECT outcome FROM decisions")["outcome"])

    def test_switching_off_mid_run(self):
        r, buyer = self.runner(["trader-scanner", "trader-timemachine"])
        r.cfg["skills"] = []
        self.decide(r)
        self.assertNotIn("skills", buyer.ctx)
        self.assertIn("AAPL", r.broker.positions)

    def test_crashing_checks_fail_closed(self):
        r, _ = self.runner(["trader-timemachine"])
        with mock.patch.object(skills, "check_entry", side_effect=KeyError("x")):
            self.decide(r)
        self.assertEqual(r.broker.positions, {})


class FleetAndApi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        text = (ROOT / "config.toml").read_text(encoding="utf-8")
        text = text.replace('db = "data/arena.db"', f'db = "{(self.tmp / "t.db").as_posix()}"').replace('brain = "auto"', 'brain = "rules"')
        self.cfgfile = self.tmp / "config.toml"
        self.cfgfile.write_text(text, encoding="utf-8")

    def test_each_agent_keeps_its_own_selection(self):
        f = Fleet(self.cfgfile)
        a = f.spawn({"region": "US", "skills": ["trader-news"]}, start=False)
        b = f.spawn({"region": "US"}, start=False)
        self.assertEqual(f.skills_of(a)["selected"], ["trader-news"])
        self.assertEqual(f.skills_of(b)["selected"], skills.DEFAULT)
        f.set_skill(a, "trader-exit", True)
        f.set_skill(a, "trader-news", False)
        self.assertEqual(Fleet(self.cfgfile).skills_of(a)["selected"], ["trader-exit"])   # saved
        self.assertEqual(f.skills_of(b)["selected"], skills.DEFAULT)                       # untouched

    def test_running_agent_gets_the_change(self):
        f = Fleet(self.cfgfile)
        rid = f.spawn({"region": "US", "skills": []}, start=False)
        f.handles[rid] = mock.Mock(runner=mock.Mock(cfg={}))
        f.set_skill(rid, "trader-scanner", True)
        self.assertEqual(f.handles[rid].runner.cfg["skills"], ["trader-scanner"])

    def test_presets_bring_the_skill_they_need(self):
        f = Fleet(self.cfgfile)
        self.assertIn("short-seller", f.skills_of(f.spawn({"preset": "bear", "region": "US", "skills": []}, start=False))["selected"])
        self.assertIn("emotion-analyst", f.skills_of(f.spawn({"preset": "crowd", "region": "US", "skills": []}, start=False))["selected"])

    def test_http(self):
        with mock.patch.object(Controller, "_claude_info", lambda *a, **k: {}):
            ctl = Controller(self.cfgfile)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def post(path, body, **h):
            req = urllib.request.Request(base + path, json.dumps(body).encode(), h)
            try:
                return json.load(urllib.request.urlopen(req))
            except urllib.error.HTTPError as e:
                return {"code": e.code}
        try:
            got = json.load(urllib.request.urlopen(base + "/api/skills?region=US"))
            self.assertEqual(got["selected"], skills.DEFAULT)
            self.assertEqual(post("/api/skills", {"key": "trader-exit", "on": True}, Origin=base)["msg"], "ok")
            self.assertIn("trader-exit", load_config(self.cfgfile)["skills"]["enabled"])
            self.assertIn("Error", post("/api/skills", {"key": "zzz", "on": True})["msg"])
            self.assertIn("Error", post("/api/skills/run", {"key": "trader-exit"})["msg"])
            self.assertIn("Error", post("/api/skills/open", {"key": "trader-exit"})["msg"])
            with mock.patch.object(skills, "open_in_claude") as op:
                for bad in ("https://evil.test", "http://127.0.0.1:1", "null"):
                    self.assertEqual(post("/api/skills/open", {"key": "socratic-tutor"}, Origin=bad), {"code": 403})
                self.assertEqual(post("/api/start", {}, Host="evil.test"), {"code": 403})
                op.assert_not_called()
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
