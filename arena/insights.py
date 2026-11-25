"""Performance analytics and plain-English insights, computed from the journal."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime

from .db import Journal


import threading

_local = threading.local()


def set_currency(symbol: str) -> None:
    """Currency symbol for text written on this thread (each agent runs on its own thread)."""
    _local.symbol = symbol


def cur() -> str:
    return getattr(_local, "symbol", "₹")


def _rs(x: float) -> str:
    sign = "−" if x < 0 else ""
    return f"{sign}{cur()}{abs(x):,.0f}"


def trade_stats(trades: list[dict]) -> dict:
    n = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    gw = sum(t["net_pnl"] for t in wins)
    gl = -sum(t["net_pnl"] for t in losses)
    gross = sum(t["gross_pnl"] for t in trades)
    chg = sum(t["charges"] for t in trades)
    return {
        "trades": n,
        "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / n * 100 if n else 0.0,
        "net": sum(t["net_pnl"] for t in trades), "gross": gross, "charges": chg,
        "avg_win": gw / len(wins) if wins else 0.0,
        "avg_loss": -gl / len(losses) if losses else 0.0,
        "profit_factor": gw / gl if gl > 0 else (math.inf if gw > 0 else 0.0),
        "expectancy": sum(t["net_pnl"] for t in trades) / n if n else 0.0,
        "best": max(trades, key=lambda t: t["net_pnl"]) if trades else None,
        "worst": min(trades, key=lambda t: t["net_pnl"]) if trades else None,
        "avg_hold_win": sum(t["hold_minutes"] for t in wins) / len(wins) if wins else 0.0,
        "avg_hold_loss": sum(t["hold_minutes"] for t in losses) / len(losses) if losses else 0.0,
    }


def equity_stats(equity: list[dict], capital: float) -> dict:
    if not equity:
        return {"max_dd_pct": 0.0, "peak": capital, "sharpe": 0.0}
    peak, max_dd = capital, 0.0
    for e in equity:
        peak = max(peak, e["equity"])
        max_dd = max(max_dd, (peak - e["equity"]) / peak * 100)
    return {"max_dd_pct": max_dd, "peak": peak}


def daily_sharpe(days: list[dict]) -> float:
    rets = [d["pnl"] / d["start_equity"] for d in days if d["start_equity"]]
    if len(rets) < 2:
        return 0.0
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
    return mu / sd * math.sqrt(252) if sd else 0.0


def group(trades: list[dict], key) -> dict[str, dict]:
    g = defaultdict(list)
    for t in trades:
        g[key(t)].append(t)
    return {k: {"trades": len(v), "net": sum(t["net_pnl"] for t in v),
                "win_rate": sum(t["net_pnl"] > 0 for t in v) / len(v) * 100} for k, v in g.items()}


def confidence_calibration(j: Journal, run_id: int, day: str | None = None) -> dict:
    """Did Claude's high-confidence entries do better than its low-confidence ones?"""
    sql = ("SELECT d.confidence c, t.net_pnl p FROM decisions d JOIN orders o ON o.decision_id=d.id AND o.side IN ('BUY','SHORT') AND o.status='filled' "
           "JOIN trades t ON t.run_id=d.run_id AND t.symbol=d.symbol AND t.entry_ts=o.ts WHERE d.run_id=?")
    params = [run_id]
    if day:
        sql += " AND substr(t.exit_ts,1,10)=?"
        params.append(day)
    rows = j.q(sql, params)
    hi = [r["p"] for r in rows if r["c"] >= 0.7]
    lo = [r["p"] for r in rows if r["c"] < 0.7]
    wr = lambda xs: sum(x > 0 for x in xs) / len(xs) * 100 if xs else None
    return {"high_n": len(hi), "high_wr": wr(hi), "low_n": len(lo), "low_wr": wr(lo)}


def _observations(trades: list[dict], scope: str, j: Journal, run_id: int, day: str | None) -> list[str]:
    out = []
    if not trades:
        return [f"No trades closed {scope}. The agent stayed in cash; check whether the setups were too strict or the market was flat."]
    s = trade_stats(trades)
    out.append(f"{s['trades']} trades closed {scope}: {s['wins']} wins, {s['losses']} losses ({s['win_rate']:.0f}% win rate), net {_rs(s['net'])}.")
    if s["gross"] > 0 and s["charges"] > 0:
        share = s["charges"] / s["gross"] * 100
        if share > 30:
            out.append(f"Charges ate {share:.0f}% of gross profit ({_rs(s['charges'])}). Fewer, larger-conviction trades would keep more of the edge.")
    elif s["charges"] > 0 and s["gross"] <= 0:
        out.append(f"Gross P&L was {_rs(s['gross'])} before costs, and {_rs(s['charges'])} of charges made it worse.")
    if s["wins"] and s["losses"]:
        ratio = abs(s["avg_win"] / s["avg_loss"]) if s["avg_loss"] else float("inf")
        out.append(f"Average win {_rs(s['avg_win'])} vs average loss {_rs(s['avg_loss'])} (reward/risk {ratio:.2f}).")
        if s["avg_hold_win"] < s["avg_hold_loss"] * 0.7:
            out.append(f"Winners were held {s['avg_hold_win']:.0f} min vs losers {s['avg_hold_loss']:.0f} min: profits are cut early while losses run.")
    reasons = group(trades, lambda t: t["exit_reason"])
    if "stop_loss" in reasons and reasons["stop_loss"]["trades"] >= max(2, s["trades"] * 0.4):
        out.append(f"{reasons['stop_loss']['trades']} of {s['trades']} exits were stop-losses ({_rs(reasons['stop_loss']['net'])}). Entries may be too early, or stops too tight for the volatility.")
    if "target" in reasons:
        out.append(f"{reasons['target']['trades']} trades reached their profit target ({_rs(reasons['target']['net'])}).")
    if "kill_switch" in reasons:
        out.append("The daily loss limit was hit and the kill switch closed everything.")
    by_sym = group(trades, lambda t: t["symbol"])
    if len(by_sym) > 1:
        best = max(by_sym.items(), key=lambda kv: kv[1]["net"])
        worst = min(by_sym.items(), key=lambda kv: kv[1]["net"])
        out.append(f"Best stock: {best[0]} ({_rs(best[1]['net'])}). Worst: {worst[0]} ({_rs(worst[1]['net'])}).")
    by_hour = group(trades, lambda t: t["entry_ts"][11:13] + ":00")
    if len(by_hour) > 1:
        bh = max(by_hour.items(), key=lambda kv: kv[1]["net"])
        wh = min(by_hour.items(), key=lambda kv: kv[1]["net"])
        if wh[1]["net"] < 0:
            out.append(f"Entries around {wh[0]} lost the most ({_rs(wh[1]['net'])}); entries around {bh[0]} did best ({_rs(bh[1]['net'])}).")
    cal = confidence_calibration(j, run_id, day)
    if cal["high_n"] >= 2 and cal["low_n"] >= 2:
        verdict = "is well calibrated" if cal["high_wr"] > cal["low_wr"] else "is NOT calibrated (high confidence did not win more often)"
        out.append(f"Claude's confidence {verdict}: {cal['high_wr']:.0f}% win rate at >=0.7 vs {cal['low_wr']:.0f}% below.")
    return out


def day_insights(j: Journal, run_id: int, day: str, start_eq: float, end_eq: float) -> list[str]:
    trades = j.q("SELECT * FROM trades WHERE run_id=? AND substr(exit_ts,1,10)=?", (run_id, day))
    pnl = end_eq - start_eq
    head = f"Day result {_rs(pnl)} ({pnl / start_eq * 100:+.2f}%)."
    return [head] + _observations(trades, "today", j, run_id, day)


def run_insights(j: Journal, run_id: int) -> list[str]:
    trades = j.q("SELECT * FROM trades WHERE run_id=? ORDER BY id", (run_id,))
    days = j.q("SELECT * FROM days WHERE run_id=? ORDER BY date", (run_id,))
    out = _observations(trades, "in this run", j, run_id, None)
    if len(days) >= 2:
        green = sum(d["pnl"] > 0 for d in days)
        out.insert(0, f"{green} of {len(days)} days were green.")
        streak, best_streak = 0, 0
        for d in days:
            streak = streak + 1 if d["pnl"] > 0 else 0
            best_streak = max(best_streak, streak)
        if best_streak >= 3:
            out.append(f"Longest winning streak: {best_streak} days.")
    dec = j.one("SELECT COUNT(*) n, COUNT(DISTINCT ts) cycles, SUM(action='HOLD') h FROM decisions WHERE run_id=?", (run_id,))
    if dec["n"]:
        who = j.one("SELECT brain FROM runs WHERE id=?", (run_id,))["brain"]
        who = "Claude" if who.startswith("claude") else "The rules baseline"
        out.append(f"{who} reviewed the watchlist {dec['cycles']} times and made {dec['n']} stock-level calls; "
                   f"{dec['h'] / dec['n'] * 100:.0f}% were HOLD, {dec['n'] - dec['h']} were trades.")
    rej = j.one("SELECT COUNT(*) n FROM orders WHERE run_id=? AND status='rejected'", (run_id,))["n"]
    if rej:
        out.append(f"{rej} order(s) were rejected by the engine's risk rules (cash, position limits, timing).")
    return out


AI_SYSTEM = """You are a trading coach reviewing a PAPER-TRADING run of an AI agent on stocks for a YouTube/Instagram
series called Breaking Money. Write a punchy, honest review in Markdown with these sections:
## Verdict (2 sentences)  ## What worked  ## What hurt  ## Patterns in the AI's decisions  ## Changes for tomorrow (3 concrete rule changes)
Use the numbers given. No hype. Never recommend a security or suggest anyone can earn these results. End with one line: simulation with virtual
money, education only, not investment advice."""


def ai_review(brain, j: Journal, run_id: int) -> str:
    from . import compliance
    run = j.one("SELECT * FROM runs WHERE id=?", (run_id,))
    compliance.refuse_recent(j, run)
    trades = j.q("SELECT symbol, qty, entry_ts, entry_price, exit_ts, exit_price, net_pnl, exit_reason, hold_minutes, entry_reason, side FROM trades WHERE run_id=? ORDER BY id", (run_id,))
    days = j.q("SELECT date, day_no, pnl, trades, wins, losses, charges FROM days WHERE run_id=? ORDER BY date", (run_id,))
    calls = j.q("SELECT ts, symbol, action, confidence, reason, outcome FROM decisions WHERE run_id=? AND action!='HOLD' ORDER BY id DESC LIMIT 60", (run_id,))
    payload = {"starting_capital": run["starting_capital"], "days": days, "trades": trades[-80:],
               "recent_non_hold_calls": calls, "computed_insights": run_insights(j, run_id)}
    text = brain.write(AI_SYSTEM, json.dumps(payload, default=str))
    j.x("INSERT INTO notes VALUES (?,?,?,?)", (run_id, datetime.now().isoformat(timespec="seconds"), "ai_review", text))
    return text
