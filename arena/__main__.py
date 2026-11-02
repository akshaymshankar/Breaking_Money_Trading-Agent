"""Command line: python -m arena <command>"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

from .clock import IST, now_ist
from .config import ROOT, load_config, load_env, set_env_value
from .db import Journal
from .safety import RealMoneyBlocked, install_guards

log = logging.getLogger("arena")


def setup_logging(cfg: dict) -> None:
    Path(cfg["paths"]["log"]).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(cfg["paths"]["log"], encoding="utf-8")):
        h.setFormatter(fmt)
        root.addHandler(h)
    for noisy in ("httpx", "yfinance", "urllib3", "anthropic", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # its errors are reported by our own retry/warn messages


def _brain_cfg(cfg: dict, brain: str | None) -> dict:
    cfg = copy.deepcopy(cfg)
    if brain:
        cfg["strategy"]["brain"] = brain
    if cfg["strategy"]["brain"] == "auto":
        from .brain import find_claude
        cfg["strategy"]["brain"] = ("claude-cli" if find_claude()
                                    else "claude-api" if os.environ.get("ANTHROPIC_API_KEY") else "rules")
    return cfg


def _open(path: str | Path, no_open: bool) -> None:
    print(f"\nResults page: {path}")
    if not no_open:
        try:
            webbrowser.open(Path(path).resolve().as_uri())
        except Exception:
            pass


def _make_run(j: Journal, cfg: dict, name: str, mode: str, feed_name: str, started) -> int:
    st = cfg["strategy"]
    return j.new_run(name, mode, feed_name, st["brain"], st.get("model", ""), cfg["account"]["starting_capital"], cfg, started.isoformat())


# ---- commands ----------------------------------------------------------
def cmd_run(a, cfg):
    from .feeds import make_live_feed
    from .runner import Runner

    cfg = _brain_cfg(cfg, a.brain)
    j = Journal(cfg["paths"]["db"])
    feed = make_live_feed(cfg)
    run = None if a.new else j.active_run("live")
    if run:
        run_id = run["id"]
        log.info("Resuming live run #%d '%s'", run_id, run["name"])
    else:
        run_id = _make_run(j, cfg, a.name or f"Live challenge {now_ist():%d %b %Y}", "live", feed.name, now_ist())
        log.info("Started live run #%d with Rs %s virtual capital", run_id, f"{cfg['account']['starting_capital']:,}")
    log.info("Feed: %s | Brain: %s | PAPER ONLY", feed.name, cfg["strategy"]["brain"])
    log.info("Results page (auto-refreshes): %s", cfg["paths"]["report"])
    try:
        Runner(cfg, feed, j, run_id).run()
    except KeyboardInterrupt:
        log.info("Stopped by you. Positions and history are saved; `python -m arena run` resumes.")
        from .report import write
        write(j, run_id, cfg["paths"]["report"], live=False)


def cmd_carry_over(a, cfg):
    """Start a fresh live run that already has earlier days' closing equity,
    so the challenge continues from the next day (paper only, no positions)."""
    from datetime import timedelta
    from .clock import is_trading_day

    holidays = set(cfg.get("market", {}).get("holidays", []))
    j = Journal(cfg["paths"]["db"])
    for r in j.q("SELECT id FROM runs WHERE status='active' AND mode='live'"):
        j.x("UPDATE runs SET status='archived', ended_at=? WHERE id=?", (now_ist().isoformat(), r["id"]))
    dates, d = [], now_ist().date() - timedelta(days=1)
    while len(dates) < len(a.equity):
        if is_trading_day(d, holidays):
            dates.insert(0, d)
        d -= timedelta(days=1)
    cap = cfg["account"]["starting_capital"]
    run_id = _make_run(j, cfg, a.name or f"Live challenge from {dates[0]:%d %b %Y}", "live", cfg["market"]["feed"], now_ist())
    prev = cap
    for n, (day, eq) in enumerate(zip(dates, a.equity), 1):
        j.x("INSERT INTO days VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, day.isoformat(), n, round(prev, 2), eq, round(eq - prev, 2), 0, 0, 0, 0, 0,
             json.dumps(["Carried over from an earlier session (no trade detail recorded)."])))
        j.x("INSERT INTO equity VALUES (?,?,?,?,?,?,?,?)",
            (run_id, f"{day.isoformat()}T15:30:00+05:30", eq, 0, eq, round(eq - cap, 2), 0, 0))
        prev = eq
    j.x("UPDATE account SET cash=?, realized=? WHERE run_id=?", (prev, round(prev - cap, 2), run_id))
    print(f"Live run #{run_id}: {len(dates)} days carried over, equity Rs {prev:,.2f}. Day {len(dates) + 1} starts at the next market open.")


def cmd_time_travel(a, cfg):
    from .clock import is_trading_day
    from .timetravel import run

    cfg = _brain_cfg(cfg, a.brain)
    if a.date:
        day = date.fromisoformat(a.date)
    else:
        day = now_ist().date() - timedelta(days=1)
        while not is_trading_day(day, set(cfg["market"].get("holidays", []))):
            day -= timedelta(days=1)
    _open(run(cfg, a.symbol, day), a.no_open)


def cmd_replay(a, cfg):
    from .feeds import load_history
    from .feeds.base import BarReplayFeed
    from .runner import Runner

    cfg = _brain_cfg(cfg, a.brain)
    bars = load_history(cfg, cfg["market"]["watchlist"])
    if not bars:
        sys.exit("No history found in data/history. Run `python -m arena fetch` first.")
    start = datetime.combine(date.fromisoformat(a.start), datetime.min.time(), IST) if a.start else None
    end = datetime.combine(date.fromisoformat(a.end), datetime.max.time(), IST) if a.end else None
    feed = BarReplayFeed(bars, start, end)
    days = sorted({t.date() for t in feed.timeline})
    if a.days:
        days = days[-a.days:]
        feed = BarReplayFeed(bars, datetime.combine(days[0], datetime.min.time(), IST), end)
    cfg["account"]["challenge_days"] = len(days)
    j = Journal(cfg["paths"]["db"])
    run_id = _make_run(j, cfg, a.name or f"Replay {days[0]} to {days[-1]} ({cfg['strategy']['brain']})", "replay", "history", feed.now())
    out = Path(cfg["paths"]["data_dir"]).parent / "reports" / f"replay_run{run_id}.html"
    log.info("Replaying %d trading days (%s to %s) with %s", len(days), days[0], days[-1], cfg["strategy"]["brain"])
    Runner(cfg, feed, j, run_id, report_path=out).run()
    _open(out, a.no_open)
    return run_id


def cmd_demo(a, cfg):
    from .feeds.sim import SimFeed
    from .runner import Runner

    cfg = _brain_cfg(cfg, a.brain or "rules")  # demo stays free unless you ask for Claude
    cfg["paths"]["db"] = str(Path(cfg["paths"]["data_dir"]) / "demo.db")
    feed = SimFeed(cfg["market"]["watchlist"], n_days=a.days, seed=a.seed)
    cfg["account"]["challenge_days"] = a.days
    j = Journal(cfg["paths"]["db"])
    run_id = _make_run(j, cfg, f"Demo on synthetic prices ({cfg['strategy']['brain']})", "demo", "synthetic", feed.now())
    out = Path(cfg["paths"]["data_dir"]).parent / "reports" / "demo.html"
    log.info("DEMO: %d days of synthetic prices, brain=%s. Not real market data.", a.days, cfg["strategy"]["brain"])
    Runner(cfg, feed, j, run_id, report_path=out).run()
    _open(out, a.no_open)


def cmd_fetch(a, cfg):
    from .feeds import save_history

    syms = cfg["market"]["watchlist"]
    if a.source == "upstox":
        from .feeds.upstox import UpstoxFeed
        bars = UpstoxFeed(cache_dir=cfg["paths"]["data_dir"]).history(syms, days=a.days)
    else:
        from .feeds.yahoo import YahooFeed
        period, interval = ("7d", "1m") if a.interval == "1m" else (f"{min(a.days, 59)}d", "5m")
        bars = YahooFeed().history(syms, period=period, interval=interval)
    paths = save_history(cfg, bars)
    for s, df in bars.items():
        print(f"  {s:12s} {len(df):6d} bars  {df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d}")
    print(f"Saved {len(paths)} files to {Path(cfg['paths']['data_dir']) / 'history'}")


def cmd_report(a, cfg):
    from .brain import make_brain
    from .insights import ai_review
    from .report import write

    db = cfg["paths"]["db"] if not a.demo else str(Path(cfg["paths"]["data_dir"]) / "demo.db")
    j = Journal(db)
    run = j.one("SELECT * FROM runs WHERE id=?", (a.run,)) if a.run else j.latest_run()
    if not run:
        sys.exit("No runs yet.")
    if a.ai:
        brain_name = a.brain or (cfg["strategy"]["brain"] if cfg["strategy"]["brain"] != "rules" else "claude-api")
        print("Asking Claude for a coach review...")
        print(ai_review(make_brain(_brain_cfg(cfg, brain_name)), j, run["id"]))
    out = a.out or (cfg["paths"]["report"] if run["mode"] == "live" else Path(cfg["paths"]["data_dir"]).parent / "reports" / f"{run['mode']}_run{run['id']}.html")
    write(j, run["id"], out, live=False)
    _open(out, a.no_open)


def cmd_status(a, cfg):
    j = Journal(cfg["paths"]["db"])
    rows = j.q("SELECT r.id, r.name, r.mode, r.brain, r.status, r.starting_capital, a.cash, a.realized, a.charges "
               "FROM runs r JOIN account a ON a.run_id=r.id ORDER BY r.id DESC LIMIT 20")
    if not rows:
        print("No runs yet.")
    for r in rows:
        pos = j.q("SELECT symbol, qty, avg_price, last_price FROM positions WHERE run_id=?", (r["id"],))
        eq = r["cash"] + sum(p["qty"] * (p["last_price"] or p["avg_price"]) for p in pos)
        days = j.one("SELECT COUNT(*) n FROM days WHERE run_id=?", (r["id"],))["n"]
        print(f"#{r['id']:<3} {r['status']:8s} {r['mode']:7s} {r['brain']:10s} days={days:<3} equity=Rs {eq:,.2f} "
              f"P&L={eq - r['starting_capital']:+,.2f} charges={r['charges']:,.2f}  {r['name']}")


def cmd_compete(a, cfg):
    brains = [b.strip() for b in a.brains.split(",") if b.strip()]
    ids = []
    for b in brains:
        ns = argparse.Namespace(brain=b, start=a.start, end=a.end, days=a.days, name=f"Compete: {b}", no_open=True)
        ids.append((b, cmd_replay(ns, cfg)))
    j = Journal(cfg["paths"]["db"])
    print("\nLEADERBOARD (same prices, same capital)")
    for b, rid in sorted(ids, key=lambda x: -j.one("SELECT realized FROM account WHERE run_id=?", (x[1],))["realized"]):
        acc = j.one("SELECT * FROM account WHERE run_id=?", (rid,))
        print(f"  {b:12s} run #{rid}: net P&L Rs {acc['realized']:+,.2f}  charges Rs {acc['charges']:,.2f}")


def cmd_upstox_login(a, cfg):
    from .feeds.upstox import login_flow

    key = os.environ.get("UPSTOX_API_KEY") or input("Upstox API key: ").strip()
    secret = os.environ.get("UPSTOX_API_SECRET") or input("Upstox API secret: ").strip()
    redirect = os.environ.get("UPSTOX_REDIRECT_URI") or "http://127.0.0.1:5055/callback"
    token = login_flow(key, secret, redirect)
    set_env_value("UPSTOX_API_KEY", key)
    set_env_value("UPSTOX_API_SECRET", secret)
    set_env_value("UPSTOX_REDIRECT_URI", redirect)
    set_env_value("UPSTOX_ACCESS_TOKEN", token)
    print("Saved UPSTOX_ACCESS_TOKEN to .env (valid until early tomorrow morning).")


def cmd_check(a, cfg):
    from .safety import SELF_TEST_URLS, check_url

    ok = True
    for url in SELF_TEST_URLS:
        try:
            check_url("POST", url)
            print(f"  FAIL: {url} was not blocked")
            ok = False
        except RealMoneyBlocked:
            print(f"  ok   real-order URL blocked: {url}")
    try:
        __import__("kiteconnect")
        print("  FAIL: broker SDK import was allowed")
        ok = False
    except RealMoneyBlocked:
        print("  ok   broker SDK imports blocked")
    except ImportError:
        print("  ok   broker SDK imports blocked")
    try:
        from .feeds import make_live_feed
        feed = make_live_feed(cfg)
        q = feed.quotes(cfg["market"]["watchlist"][:3])
        for s, x in q.items():
            print(f"  ok   {feed.name}: {s} {x.ltp:.2f} ({x.change_pct:+.2f}%) at {x.ts:%d %b %H:%M}")
        if not q:
            print(f"  WARN {feed.name}: no quotes returned"); ok = False
    except Exception as e:
        print(f"  FAIL market data: {e}"); ok = False
    brain = _brain_cfg(cfg, None)["strategy"]["brain"]
    print(f"  info using brain: {brain}" + ("  (free rules strategy; install Claude Code to let Claude trade)" if brain == "rules" else ""))
    if brain == "claude-api":
        print("  ok   ANTHROPIC_API_KEY set" if os.environ.get("ANTHROPIC_API_KEY") else "  WARN ANTHROPIC_API_KEY not set (or use `ant auth login`)")
    elif brain == "claude-cli":
        from .brain import find_claude
        exe = find_claude()
        print(f"  ok   claude CLI found: {exe}" if exe else "  FAIL claude CLI not found"); ok = ok and bool(exe)
    print("\nAll good." if ok else "\nSome checks need attention.")


def cmd_gui(a, cfg):
    from .gui import serve
    serve(a.port, not a.no_open)


def main(argv=None):
    load_env()
    p = argparse.ArgumentParser(prog="arena", description="Breaking Money AI Trading Arena (paper trading only)")
    p.add_argument("--config", help="path to config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="live paper-trading session on real-time prices")
    r.add_argument("--new", action="store_true", help="start a fresh run instead of resuming")
    r.add_argument("--name"); r.add_argument("--brain", choices=["auto", "claude-api", "claude-cli", "rules"])

    rp = sub.add_parser("replay", help="replay saved history without lookahead")
    rp.add_argument("--start"); rp.add_argument("--end"); rp.add_argument("--days", type=int)
    rp.add_argument("--name"); rp.add_argument("--brain", choices=["auto", "claude-api", "claude-cli", "rules"])
    rp.add_argument("--no-open", action="store_true")

    d = sub.add_parser("demo", help="offline demo on synthetic prices")
    d.add_argument("--days", type=int, default=10); d.add_argument("--seed", type=int, default=7)
    d.add_argument("--brain", choices=["auto", "claude-api", "claude-cli", "rules"]); d.add_argument("--no-open", action="store_true")

    f = sub.add_parser("fetch", help="download history for replay")
    f.add_argument("--source", choices=["yahoo", "upstox"], default="yahoo")
    f.add_argument("--days", type=int, default=30); f.add_argument("--interval", choices=["1m", "5m"], default="5m")

    rep = sub.add_parser("report", help="rebuild the results page")
    rep.add_argument("--run", type=int); rep.add_argument("--out"); rep.add_argument("--demo", action="store_true")
    rep.add_argument("--ai", action="store_true", help="add Claude's coach review")
    rep.add_argument("--brain", choices=["claude-api", "claude-cli"]); rep.add_argument("--no-open", action="store_true")

    sub.add_parser("status", help="list runs and their P&L")
    tt = sub.add_parser("time-travel", help="replay one stock's past day bar by bar (no lookahead)")
    tt.add_argument("symbol", help="NSE symbol, e.g. LT")
    tt.add_argument("--date", help="YYYY-MM-DD (default: previous trading day)")
    tt.add_argument("--brain", choices=["auto", "claude-api", "claude-cli", "rules"])
    tt.add_argument("--no-open", action="store_true")
    co = sub.add_parser("carry-over", help="start a live run from earlier days' closing equity")
    co.add_argument("equity", type=float, nargs="+", help="closing equity of day 1, day 2, ...")
    co.add_argument("--name")
    c = sub.add_parser("compete", help="several brains on the same replay data")
    c.add_argument("--brains", default="rules,claude-api"); c.add_argument("--start"); c.add_argument("--end"); c.add_argument("--days", type=int)
    sub.add_parser("upstox-login", help="get today's Upstox market-data token")
    sub.add_parser("check", help="test safety guard, market data and Claude setup")
    g = sub.add_parser("gui", help="open the control panel in your browser")
    g.add_argument("--port", type=int, default=8787); g.add_argument("--no-open", action="store_true")

    a = p.parse_args(argv)
    cfg = load_config(a.config)
    install_guards(cfg)
    setup_logging(cfg)
    {"run": cmd_run, "replay": cmd_replay, "demo": cmd_demo, "fetch": cmd_fetch, "report": cmd_report,
     "status": cmd_status, "carry-over": cmd_carry_over, "time-travel": cmd_time_travel, "compete": cmd_compete, "upstox-login": cmd_upstox_login, "check": cmd_check, "gui": cmd_gui}[a.cmd](a, cfg)


if __name__ == "__main__":
    main()
