"""Who pays for Claude. Offline: python -m unittest discover tests"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import launch  # noqa: E402
from arena import brain, skills  # noqa: E402
from arena.config import load_config  # noqa: E402
from arena.db import Journal  # noqa: E402
from arena.feeds.sim import SimFeed  # noqa: E402
from arena.fleet import Fleet  # noqa: E402
from arena.markets import market_of  # noqa: E402
from arena.runner import Runner  # noqa: E402

PAID = {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_AUTH_TOKEN": "tok", "ANTHROPIC_PROFILE": "work",
        "ANTHROPIC_FEDERATION_RULE_ID": "r", "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1",
        "CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_BASE_URL": "https://x.test", "ANTHROPIC_CUSTOM_HEADERS": "x: y"}
REPLY = {"result": "READY", "is_error": False, "total_cost_usd": 0.06,
         "usage": {"input_tokens": 100, "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 900, "output_tokens": 700},
         "modelUsage": {"claude-opus-5-5": {}}}


def auth(method, sub="pro"):
    return {"loggedIn": True, "authMethod": method, **({"subscriptionType": sub} if sub else {})}


def fake_cli(calls, status=None, reply=None):
    """A stand-in for the claude program: records each call's environment."""
    def run(cmd, **kw):
        calls.append((cmd, kw))
        body = (status or auth("claude.ai")) if "auth" in cmd else (reply or REPLY)
        lines = body if isinstance(body, list) else [body]
        return subprocess.CompletedProcess(cmd, 0, "\n".join(json.dumps(x) for x in lines), "")
    return run


class Base(unittest.TestCase):
    def setUp(self):
        self.calls = []
        brain._billing_cache.clear()
        brain._limits.clear()
        self.addCleanup(brain._billing_cache.clear)
        self.addCleanup(brain._limits.clear)
        for p in (mock.patch.dict(os.environ, {**PAID, "KEEP_ME": "1"}), mock.patch.object(brain, "find_claude", lambda: "claude")):
            p.start()
            self.addCleanup(p.stop)

    def cli(self, **kw):
        return mock.patch.object(brain.subprocess, "run", side_effect=fake_cli(self.calls, **kw))

    def assertNoPaid(self, env):
        self.assertIsNotNone(env, "the claude program must not inherit the whole environment")
        self.assertFalse(set(PAID) & {k.upper() for k in env})
        self.assertEqual(env["KEEP_ME"], "1")


class PlanOnly(Base):
    def test_decisions_never_see_a_paid_key(self):
        with self.cli():
            brain.ClaudeCLIBrain({"strategy": {}})._run("hi")
        self.assertTrue(self.calls)
        for _, kw in self.calls:
            self.assertNoPaid(kw.get("env"))

    def test_the_apps_own_environment_is_left_alone(self):
        with self.cli():
            brain.ClaudeCLIBrain({"strategy": {}})._run("hi")
        self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "sk-test")

    def test_sign_in_check_does_not_count_a_key_as_a_login(self):
        with self.cli():
            brain.claude_logged_in("claude")
        self.assertNoPaid(self.calls[-1][1].get("env"))

    def test_launcher_sign_in_check_ignores_a_key(self):
        with mock.patch.object(launch.subprocess, "run", side_effect=fake_cli(self.calls)):
            launch.logged_in("claude")
        self.assertNoPaid(self.calls[-1][1].get("env"))

    @unittest.skipUnless(os.name == "nt", "Windows console windows")
    def test_sign_in_and_skill_windows_ignore_a_key(self):
        with mock.patch.object(brain.subprocess, "Popen") as p:
            brain.open_terminal(["claude", "auth", "login"])
            self.assertNoPaid(p.call_args.kwargs.get("env"))
            skills.open_in_claude("socratic-tutor")
            self.assertNoPaid(p.call_args.kwargs.get("env"))

    def test_mac_terminal_drops_the_key_too(self):
        # A new Terminal window reads the shell profile again, so the variables are unset inside it.
        with mock.patch.object(brain.subprocess, "Popen") as p, mock.patch("sys.platform", "darwin"), \
                mock.patch("os.name", "posix"), mock.patch.object(brain, "_neutral_dir", lambda: "."):
            brain.open_terminal(["claude", "auth", "login"])
        script = " ".join(p.call_args.args[0])
        self.assertIn("-u ANTHROPIC_API_KEY", script)
        self.assertIn("claude auth login", script)

    def test_connection_test_uses_the_plan(self):
        with self.cli():
            st = brain.claude_status(run_ping=True)
        self.assertTrue(st["ok"])
        for _, kw in self.calls:
            self.assertNoPaid(kw.get("env"))

    def test_connection_test_uses_the_key_only_when_chosen(self):
        with self.cli(), mock.patch.object(brain, "ClaudeAPIBrain") as api:
            api.return_value._call.return_value = "READY"
            brain.claude_status(run_ping=True)
            api.assert_not_called()
            brain.claude_status(run_ping=True, brain="claude-api")
            api.assert_called_once()


class Automatic(Base):
    def test_automatic_never_picks_a_paid_key(self):
        self.assertEqual(brain.auto_brain(), "claude-cli")
        with mock.patch.object(brain, "find_claude", lambda: None):
            self.assertEqual(brain.auto_brain(), "rules")
            cfg = load_config()
            cfg["strategy"]["brain"] = "auto"
            self.assertEqual(brain.make_brain(cfg).name, "rules")
            from arena.__main__ import _brain_cfg
            self.assertEqual(_brain_cfg(load_config(overrides={"strategy": {"brain": "auto"}}), None)["strategy"]["brain"], "rules")

    def test_spawning_an_automatic_agent_never_picks_a_paid_key(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.object(brain, "find_claude", lambda: None):
            f = Fleet()
            cfg = load_config()
            cfg["paths"].update(db=f"{tmp}/t.db")
            j = Journal(cfg["paths"]["db"])
            with mock.patch.object(Fleet, "base_cfg", lambda s, o=None: cfg), mock.patch.object(Fleet, "journal", lambda s: j):
                rid = f.spawn({"region": "US", "brain": "auto"}, start=False)
            self.assertEqual(j.one("SELECT brain FROM runs WHERE id=?", (rid,))["brain"], "rules")
            j.conn.close()

    def test_the_key_still_works_when_chosen(self):
        with mock.patch.object(brain, "ClaudeAPIBrain") as api:
            brain.make_brain({"strategy": {"brain": "claude-api"}})
        api.assert_called_once()


class WhoPays(Base):
    def payer(self, status):
        brain._billing_cache.clear()
        with self.cli(status=status):
            return brain.billing("claude")

    def test_labels(self):
        self.assertEqual(self.payer(auth("claude.ai", "pro")), {"paid_by": "plan", "label": "Claude Pro plan"})
        self.assertEqual(self.payer(auth("claude.ai", "max"))["label"], "Claude Max plan")
        self.assertEqual(self.payer(auth("oauth_token", None)), {"paid_by": "plan", "label": "Claude plan"})
        self.assertEqual(self.payer(auth("claude.ai", None))["paid_by"], "")  # signed in, but no plan named
        self.assertEqual(self.payer(auth("api_key", None))["paid_by"], "api")
        self.assertEqual(self.payer(auth("api_key_helper", None))["paid_by"], "api")
        self.assertEqual(self.payer({"loggedIn": False})["paid_by"], "")
        self.assertEqual(self.payer({"loggedIn": True})["paid_by"], "")  # never claim the plan without proof

    def test_status_names_the_payer(self):
        with self.cli():
            self.assertEqual(brain.claude_status()["paid_by"], "plan")
            self.assertEqual(brain.claude_status(brain="claude-api")["paid_by"], "api")
            self.assertEqual(brain.claude_status(brain="rules")["paid_by"], "none")

    def test_an_automatic_app_without_claude_code_pays_nobody(self):
        with mock.patch.object(brain, "find_claude", lambda: None):
            self.assertEqual(brain.claude_status(brain="auto")["paid_by"], "none")


class Usage(Base):
    def runner(self, tmp, b):
        cfg = load_config(overrides={"market": {"region": "US"}})
        cfg["paths"].update(db=f"{tmp}/t.db", report=f"{tmp}/r.html", data_dir=tmp, log=f"{tmp}/l.log")
        cfg["strategy"]["brain"] = "claude-cli"
        j = Journal(cfg["paths"]["db"])
        feed = SimFeed(cfg["market"]["watchlist"], n_days=1, market=market_of(cfg))
        rid = j.new_run("t", "live", "sim", "claude-cli", "", 100000, cfg, feed.now().isoformat())
        return Runner(cfg, feed, j, rid, brain=b), j, rid

    def test_each_call_is_saved_for_its_agent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, self.cli():
            b = brain.ClaudeCLIBrain({"strategy": {}})
            _, j, rid = self.runner(tmp, b)
            b._run("one")
            b._run("two")
            rows = j.q("SELECT * FROM usage WHERE run_id=?", (rid,))
            self.assertEqual(len(rows), 2)
            r = rows[0]
            self.assertEqual((r["paid_by"], r["model"], r["input_tokens"], r["output_tokens"], r["list_usd"]),
                             ("plan", "claude-opus-5-5", 5000, 700, 0.06))
            s = Fleet()._summary(j, j.one("SELECT * FROM runs WHERE id=?", (rid,)))
            self.assertEqual(s["usage"]["today"], {"calls": 2, "tokens": 11400, "list_usd": 0.12})
            self.assertEqual(s["usage"]["paid_by"], "plan")
            j.conn.close()

    def test_an_agent_with_no_calls_says_so(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, j, rid = self.runner(tmp, brain.RulesBrain({"strategy": {}}))
            s = Fleet()._summary(j, j.one("SELECT * FROM runs WHERE id=?", (rid,)))
            self.assertEqual(s["usage"]["today"]["calls"], 0)
            j.conn.close()

    def test_a_failed_call_that_used_tokens_still_counts(self):
        seen = []
        with self.cli(reply={**REPLY, "is_error": True, "result": "limit"}):
            b = brain.ClaudeCLIBrain({"strategy": {}})
            b.on_usage = seen.append
            with self.assertRaises(RuntimeError):
                b._run("x")
        self.assertEqual(len(seen), 1)

    def test_a_broken_meter_never_breaks_a_decision(self):
        def boom(u):
            raise OSError("disk full")
        with self.cli():
            b = brain.ClaudeCLIBrain({"strategy": {}})
            b.on_usage = boom
            self.assertEqual(b._run("x"), "READY")


def limit_event(five=.2, week=.3, using=False, resets=None):
    import time
    r = resets or time.time() + 3600
    return {"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "isUsingOverage": using, "overageStatus": "rejected",
        "unifiedWindows": {"five_hour": {"utilization": five, "resetsAt": r}, "seven_day": {"utilization": week, "resetsAt": r + 9999}}}}


def model_calls(calls):
    return [c for c, _ in calls if "-p" in c]


class PlanLock(Base):
    def run_once(self, st=None, **kw):
        with self.cli(**kw):
            return brain.ClaudeCLIBrain({"strategy": st or {}})._run("x")

    def test_refuses_a_login_that_is_billed_per_use(self):
        for status in (auth("api_key", None), auth("api_key_helper", None), {"loggedIn": True}, {"loggedIn": False}):
            brain._billing_cache.clear()
            with self.assertRaisesRegex(RuntimeError, "(?i)plan-only lock"):
                self.run_once(status=status)
        self.assertEqual(model_calls(self.calls), [])  # refused before anything was sent

    def test_refuses_when_the_sign_in_cannot_be_read(self):
        with mock.patch.object(brain.subprocess, "run", side_effect=OSError("boom")) as r:
            with self.assertRaisesRegex(RuntimeError, "(?i)plan-only lock"):
                brain.ClaudeCLIBrain({"strategy": {}})._run("x")
        self.assertFalse([c for c in r.call_args_list if "-p" in c.args[0]])

    def test_a_key_is_refused_while_the_lock_is_on(self):
        for st in ({}, {"plan_only": True}):
            with self.assertRaisesRegex(RuntimeError, "(?i)plan-only lock"):
                brain.ClaudeAPIBrain({"strategy": {"model": "m", **st}})

    def test_the_lock_can_be_switched_off_on_purpose(self):
        self.assertEqual(self.run_once({"plan_only": False}, status=auth("api_key", None)), "READY")

    def test_settings_files_and_plugins_are_left_out(self):
        self.run_once()
        cmd = model_calls(self.calls)[0]
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")  # also drops any apiKeyHelper
        self.assertIn("--strict-mcp-config", cmd)

    def test_stops_at_the_share_of_the_plan_you_allow(self):
        self.assertEqual(self.run_once(reply=[limit_event(five=.85), REPLY]), "READY")
        n = len(model_calls(self.calls))
        with self.assertRaisesRegex(RuntimeError, "80%"):
            self.run_once(reply=[limit_event(), REPLY])
        self.assertEqual(len(model_calls(self.calls)), n)

    def test_weekly_limit_counts_too_and_the_share_is_adjustable(self):
        self.run_once(reply=[limit_event(week=.6), REPLY])
        self.run_once(reply=[limit_event(week=.6), REPLY])
        with self.assertRaises(RuntimeError):
            self.run_once({"max_plan_used_pct": 50}, reply=[limit_event(), REPLY])

    def test_starts_again_after_the_limit_resets(self):
        import time
        self.run_once(reply=[limit_event(five=.95, resets=time.time() - 1), REPLY])
        self.assertEqual(self.run_once(reply=[limit_event(), REPLY]), "READY")

    def test_every_kind_of_limit_counts_and_a_missing_reset_is_not_a_pass(self):
        for info in ({"unifiedWindows": {"five_hour": {"utilization": .99}}},
                     {"unifiedWindows": {"seven_day_opus": {"utilization": .95, "resetsAt": 9e9}}},
                     {"rateLimitType": "five_hour", "utilization": .9, "resetsAt": 9e9},
                     {"rateLimitType": "seven_day", "status": "rejected", "resetsAt": 9e9}):
            brain._limits.clear()
            brain._note_limits(info)
            with self.assertRaisesRegex(RuntimeError, "(?i)plan-only lock", msg=str(info)):
                self.run_once()

    def test_paid_overage_stops_everything_at_once(self):
        self.run_once(reply=[limit_event(five=.1, using=True), REPLY])
        with self.assertRaisesRegex(RuntimeError, "usage credits"):
            self.run_once(reply=[limit_event(), REPLY])

    def test_a_refused_call_falls_back_to_the_free_rules(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, self.cli(status=auth("api_key", None)):
            cfg = load_config(overrides={"market": {"region": "US"}})
            cfg["paths"].update(db=f"{tmp}/t.db", report=f"{tmp}/r.html", data_dir=tmp, log=f"{tmp}/l.log")
            j = Journal(cfg["paths"]["db"])
            feed = SimFeed(cfg["market"]["watchlist"], n_days=1, market=market_of(cfg))
            rid = j.new_run("t", "live", "sim", "claude-cli", "", 100000, cfg, feed.now().isoformat())
            r = Runner(cfg, feed, j, rid, brain=brain.ClaudeCLIBrain(cfg))
            now = feed.now()
            r.open_day(now)
            r.decide_and_execute(now, r._quotes(r.universe))
            self.assertEqual({x["brain"] for x in j.q("SELECT brain FROM decisions")}, {"rules"})
            j.conn.close()
        self.assertEqual(model_calls(self.calls), [])

    def test_the_lock_covers_agents_made_before_it_existed(self):
        seen = {}
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            f = Fleet()
            cfg = load_config(overrides={"market": {"region": "US"}})
            cfg["strategy"].update(plan_only=False, max_plan_used_pct=100)
            cfg["paths"]["db"] = f"{tmp}/t.db"
            j = Journal(cfg["paths"]["db"])
            rid = j.new_run("t", "live", "x", "claude-cli", "", 1000, cfg, "2026-01-01T00:00:00")

            def stop(c, *a, **k):
                seen.update(c["strategy"])
                raise RuntimeError("enough")
            from arena.fleet import Handle
            with mock.patch("arena.runner.Runner", side_effect=stop), mock.patch("arena.feeds.make_live_feed"), \
                    mock.patch.object(Fleet, "db_path", f"{tmp}/t.db"), self.assertRaises(RuntimeError):
                f._run_agent(Handle(rid), j.one("SELECT * FROM runs WHERE id=?", (rid,)))
            j.conn.close()
        self.assertIs(seen["plan_only"], True)  # config.toml decides, not the agent's old copy
        self.assertEqual(seen["max_plan_used_pct"], 80)


if __name__ == "__main__":
    unittest.main()
