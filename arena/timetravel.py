"""Time travel: replay one stock's past trading day bar by bar.

The agent wakes up at 09:15 on that date and sees only the bars up to each
moment (BarReplayFeed hides the future), so it trades the day as if it were
live. The result is a single-stock dashboard. Paper only; nothing is sent
anywhere and the live run's database is never touched.
"""
from __future__ import annotations

import copy
import html
import json
import logging
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd

from .clock import IST
from .config import ROOT
from .db import Journal

log = logging.getLogger("arena")
EXITS = {"ai": "agent's exit call", "stop_loss": "stop-loss hit", "target": "target hit",
         "square_off": "3:15 PM auto-exit", "kill_switch": "daily loss limit"}


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _rs(x: float, sign: bool = False) -> str:
    s = f"{abs(x):,.2f}"
    return (("+" if x >= 0 else "−") if sign else ("−" if x < 0 else "")) + "₹" + s


def fetch_bars(symbol: str, day: date) -> pd.DataFrame:
    """5-minute bars for `day` plus the days before it (indicators need history)."""
    from .feeds.yahoo import YahooFeed
    df = YahooFeed().history([symbol], period="60d", interval="5m").get(symbol)
    if df is None or df.empty:
        raise RuntimeError(f"No price history for {symbol} from Yahoo.")
    df = df[df.index.date <= day]
    if day not in set(df.index.date):
        raise RuntimeError(f"No bars for {symbol} on {day} (holiday, weekend, or older than ~60 days).")
    return df


def run(cfg: dict, symbol: str, day: date, bars: pd.DataFrame | None = None, out_dir: Path | None = None) -> Path:
    from .feeds.base import BarReplayFeed
    from .runner import Runner

    symbol = symbol.upper().removesuffix(".NS")
    bars = fetch_bars(symbol, day) if bars is None else bars
    cfg = copy.deepcopy(cfg)
    out_dir = Path(out_dir or ROOT / "reports" / "timetravel")
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg["market"]["watchlist"] = [symbol]
    cfg["account"]["challenge_days"] = 1
    cfg["paths"]["db"] = str(Path(cfg["paths"]["data_dir"]) / "timetravel.db")
    feed = BarReplayFeed({symbol: bars}, start=datetime.combine(day, time(9, 15), IST),
                         end=datetime.combine(day, time(23, 59), IST))
    j = Journal(cfg["paths"]["db"])
    st = cfg["strategy"]
    rid = j.new_run(f"Time travel · {symbol} · {day}", "timetravel", "history", st["brain"], st.get("model", ""),
                    cfg["account"]["starting_capital"], cfg, feed.now().isoformat())
    log.info("TIME TRAVEL: %s on %s with %s, seeing only past bars at each step.", symbol, day, st["brain"])
    Runner(cfg, feed, j, rid, report_path=out_dir / "_last_run.html").run()
    out = out_dir / f"{symbol}-{day}.html"
    out.write_text(render(j, rid, symbol, day, bars), encoding="utf-8")
    log.info("Time travel dashboard: %s", out)
    return out


def forecast(bars: pd.DataFrame, at: pd.Timestamp, stop: float, target: float, n: int = 10_000, seed: int = 0) -> dict:
    """Monte Carlo of the rest of the day from `at`, built only from bars up to `at`.

    Each path resamples past 5-minute log returns (bootstrap), so it carries no
    information from the future. Returns percentile bands per remaining bar and
    the odds that price touches `target` before `stop` (target > stop for longs).
    """
    import numpy as np
    seen = bars.loc[:at]
    rets = np.diff(np.log(seen["close"].to_numpy()))[-375:]  # about the last five sessions
    day = bars[bars.index.date == at.date()]
    future = day[day.index > at].index
    if len(rets) < 20 or not len(future):
        return {}
    rng = np.random.default_rng(seed)
    start = float(seen["close"].iloc[-1])
    paths = start * np.exp(np.cumsum(rng.choice(rets, size=(n, len(future))), axis=1))
    long = target > stop
    hit_t = (paths >= target) if long else (paths <= target)
    hit_s = (paths <= stop) if long else (paths >= stop)
    first = lambda m: np.where(m.any(1), m.argmax(1), len(future))
    t_first, s_first = first(hit_t), first(hit_s)
    end = paths[:, -1]
    pct = lambda q: np.percentile(paths, q, axis=0)
    return {"times": list(future), "start": start, "n": n, "p5": pct(5), "p25": pct(25), "p50": pct(50), "p75": pct(75), "p95": pct(95),
            "p_target": float((t_first < s_first).mean()), "p_stop": float((s_first < t_first).mean()),
            "exp_move_pct": float((end.mean() / start - 1) * 100 * (1 if long else -1))}


def _chart(day_bars: pd.DataFrame, trades: list[dict], decisions: list[dict], fc: dict | None = None) -> str:
    W, H, L, R, T, B = 900, 320, 56, 12, 14, 26
    lo, hi = float(day_bars["low"].min()), float(day_bars["high"].max())
    if fc:
        lo, hi = min(lo, float(fc["p5"].min())), max(hi, float(fc["p95"].max()))
    pad = (hi - lo) * 0.06 or 1
    lo, hi = lo - pad, hi + pad
    t0 = day_bars.index[0]
    span = max((day_bars.index[-1] - t0).total_seconds(), 1)
    x = lambda ts: L + (pd.Timestamp(ts).tz_convert(t0.tz) - t0).total_seconds() / span * (W - L - R)
    y = lambda p: T + (hi - p) / (hi - lo) * (H - T - B)
    close = " ".join(f"{x(t):.1f},{y(c):.1f}" for t, c in day_bars["close"].items())
    tp = (day_bars["high"] + day_bars["low"] + day_bars["close"]) / 3
    vwap = (tp * day_bars["volume"]).cumsum() / day_bars["volume"].cumsum().replace(0, pd.NA)
    vw = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in vwap.dropna().items())
    grid = "".join(
        f'<line x1="{L}" x2="{W - R}" y1="{y(p):.1f}" y2="{y(p):.1f}" class="g"/><text x="{L - 6}" y="{y(p) + 4:.1f}" class="ax" text-anchor="end">{p:,.0f}</text>'
        for p in [lo + (hi - lo) * k / 4 for k in range(5)])
    ticks = "".join(
        f'<text x="{x(t):.1f}" y="{H - 6}" class="ax" text-anchor="middle">{t:%H:%M}</text>'
        for t in day_bars.index if t.minute == 0 and t.hour in (10, 11, 12, 13, 14, 15))
    orb = day_bars[day_bars.index.time < time(9, 45)]
    orl = "".join(
        f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="or"/><text x="{W - R}" y="{y(v) - 4:.1f}" class="ax" text-anchor="end">{lab}</text>'
        for v, lab in [(float(orb["high"].max()), "OR high"), (float(orb["low"].min()), "OR low")]) if len(orb) else ""
    dots = "".join(
        f'<circle cx="{x(d["ts"]):.1f}" cy="{H - B + 6}" r="3" class="d"><title>{_e(d["ts"][11:16])} HOLD: {_e(d["reason"])}</title></circle>'
        for d in decisions if d["action"] == "HOLD")
    marks = ""
    for t in trades:
        short = t["qty"] < 0
        for ts, p, entry in ((t["entry_ts"], t["entry_price"], True), (t["exit_ts"], t["exit_price"], False)):
            cx, cy = x(ts), y(p)
            up = entry != short  # buy legs point up, sell legs down
            pts = f"{cx},{cy - 9} {cx - 7},{cy + 5} {cx + 7},{cy + 5}" if up else f"{cx},{cy + 9} {cx - 7},{cy - 5} {cx + 7},{cy - 5}"
            lab = ("SHORT" if short else "BUY") if entry else ("COVER" if short else "SELL")
            marks += (f'<polygon points="{pts}" class="{"mb" if up else "ms"}"><title>{lab} @ {p:,.2f} · {ts[11:16]}</title></polygon>'
                      f'<text x="{cx:.1f}" y="{cy + (-14 if up else 22):.1f}" class="ml" text-anchor="middle">{lab}</text>')
        marks += f'<line x1="{x(t["entry_ts"]):.1f}" y1="{y(t["entry_price"]):.1f}" x2="{x(t["exit_ts"]):.1f}" y2="{y(t["exit_price"]):.1f}" class="{"tw" if t["net_pnl"] >= 0 else "tl"}"/>'
    band = ""
    if fc:
        def area(a, b):
            pts = [f"{x(t):.1f},{y(v):.1f}" for t, v in zip(fc["times"], a)] + [f"{x(t):.1f},{y(v):.1f}" for t, v in reversed(list(zip(fc["times"], b)))]
            return " ".join(pts)
        band = (f'<polygon points="{area(fc["p95"], fc["p5"])}" class="f90"/><polygon points="{area(fc["p75"], fc["p25"])}" class="f50"/>'
                f'<polyline points="{" ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in zip(fc["times"], fc["p50"]))}" class="fmed"/>')
    return (f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Price chart with trades">{grid}{orl}{band}'
            f'<polyline points="{vw}" class="vw"/><polyline points="{close}" class="px"/>{marks}{dots}{ticks}</svg>')


def _fc_panel(fc: dict) -> str:
    t = fc["times"]
    tiles = [("Paths simulated", f"{fc['n']:,}"), ("Target before stop", f"{fc['p_target'] * 100:.0f}%"),
             ("Stop before target", f"{fc['p_stop'] * 100:.0f}%"), ("Expected move by close", f"{fc['exp_move_pct']:+.2f}%"),
             ("Close range (90%)", f"₹{fc['p5'][-1]:,.0f} – ₹{fc['p95'][-1]:,.0f}")]
    return ('<div class="panel"><h2>Looking ahead from the entry: 10,000 possible futures</h2><div class="tiles" style="margin:0">'
            + "".join(f'<div class="tile"><span>{k}</span><b>{v}</b></div>' for k, v in tiles)
            + f'</div><p class="muted" style="margin:8px 0 0;font-size:12px">From ₹{fc["start"]:,.2f} at {t[0] - (t[1] - t[0]) if len(t) > 1 else t[0]:%H:%M}, '
            "each path replays randomly drawn 5-minute moves from the bars seen so far (about five sessions). Nothing after the entry is used, "
            "so this is what could be expected at that moment, not what happened.</p></div>")


def render(j: Journal, run_id: int, symbol: str, day: date, bars: pd.DataFrame) -> str:
    run = j.one("SELECT * FROM runs WHERE id=?", (run_id,))
    trades = j.q("SELECT * FROM trades WHERE run_id=? ORDER BY id", (run_id,))
    decisions = j.q("SELECT ts, action, confidence, reason, market_view, outcome, brain FROM decisions WHERE run_id=? ORDER BY id", (run_id,))
    eq = j.q("SELECT equity FROM equity WHERE run_id=? ORDER BY ts", (run_id,))
    cap = run["starting_capital"]
    end_eq = eq[-1]["equity"] if eq else cap
    pnl = end_eq - cap
    day_bars = bars[bars.index.date == day]
    o, c = float(day_bars["open"].iloc[0]), float(day_bars["close"].iloc[-1])
    who = "Claude" if run["brain"].startswith("claude") else "The rules strategy"
    wins = sum(t["net_pnl"] > 0 for t in trades)
    charges = sum(t["charges"] for t in trades)
    fc = {}
    if trades:
        t0 = trades[0]
        entry_ts = pd.Timestamp(t0["entry_ts"]).tz_convert(bars.index.tz)
        at = bars.index[bars.index <= entry_ts][-1]
        fc = forecast(bars, at, stop=t0["entry_price"] * (0.99 if t0["qty"] > 0 else 1.01),
                      target=t0["entry_price"] * (1.02 if t0["qty"] > 0 else 0.98))
    lev = (json.loads(run["config_json"]).get("strategy", {}) or {}).get("leverage", 1)

    tiles = [("Day P&L", _rs(pnl, True), "up" if pnl > 0 else "down" if pnl < 0 else ""),
             ("Return", f"{pnl / cap * 100:+.2f}%", "up" if pnl > 0 else "down" if pnl < 0 else ""),
             (f"{symbol} open → close", f"{(c / o - 1) * 100:+.2f}%", "up" if c > o else "down"),
             ("Trades", f"{len(trades)} ({wins} won)", ""),
             ("Calls made", str(len(decisions)), ""),
             ("Charges", _rs(charges), "")]
    tiles_html = "".join(f'<div class="tile"><span>{k}</span><b class="{cl}">{v}</b></div>' for k, v, cl in tiles)

    def card(t):
        short = t["qty"] < 0
        return (f'<div class="card"><div class="ch"><b>{"SHORT" if short else "LONG"} {abs(t["qty"])} {symbol}</b>'
                f'<b class="{"up" if t["net_pnl"] >= 0 else "down"}">{_rs(t["net_pnl"], True)}</b></div>'
                f'<div class="kv"><span>{"Shorted" if short else "Bought"}</span>₹{t["entry_price"]:,.2f} at {t["entry_ts"][11:16]}</div>'
                f'<div class="kv"><span>{"Bought back" if short else "Sold"}</span>₹{t["exit_price"]:,.2f} at {t["exit_ts"][11:16]} · {EXITS.get(t["exit_reason"], t["exit_reason"])}</div>'
                f'<div class="kv"><span>Size</span>{_rs(abs(t["qty"]) * t["entry_price"])} · charges {_rs(t["charges"])}</div>'
                f'<p><span class="lbl">Why {who.split()[0] if who == "Claude" else "it"} entered:</span> {_e(t["entry_reason"])}</p></div>')

    rows = "".join(
        f'<tr class="{d["action"].lower()}"><td>{_e(d["ts"][11:16])}</td><td><b>{_e(d["action"])}</b></td>'
        f'<td class="n">{float(d["confidence"] or 0):.2f}</td><td>{_e(d["reason"])}</td><td class="muted">{_e(d["outcome"])}</td></tr>'
        for d in decisions)
    views = [d["market_view"] for d in decisions if d["market_view"]]
    view_html = f'<p class="view">“{_e(views[-1])}”</p>' if views else ""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Time travel · {_e(symbol)} · {day}</title><style>
:root{{--page:#08090c;--surface:#12141a;--surface-2:#1a1d25;--line:#262a35;--ink:#f0efec;--ink-2:#c3c2b7;--muted:#898781;
--accent:#3987e5;--up:#0ca30c;--down:#d03b3b;--warn:#fab219;--violet:#9085e9}}
*{{box-sizing:border-box}}html,body{{margin:0}}
body{{background:radial-gradient(1200px 600px at 20% -10%,#132036 0,transparent 60%),var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding:20px 16px 60px}}
.wrap{{max-width:1100px;margin:0 auto}}
header{{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin-bottom:16px}}
.brand{{font-weight:800;letter-spacing:.14em;text-transform:uppercase;font-size:12px;color:var(--accent)}}
h1{{margin:2px 0 0;font-size:24px}}.sub{{color:var(--muted);font-size:12px}}
.badge{{border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--violet)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}}
.tile,.panel,.card{{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px 14px}}
.tile span{{display:block;color:var(--muted);font-size:11.5px}}.tile b{{font-size:20px}}
.up{{color:var(--up)}}.down{{color:var(--down)}}.muted{{color:var(--muted)}}
.panel{{margin-bottom:14px}}.panel h2{{font-size:14px;margin:0 0 8px;color:var(--ink-2)}}
.chart{{width:100%;height:auto;display:block}}
.chart .g{{stroke:var(--line)}}.chart .ax{{fill:var(--muted);font-size:10.5px}}
.chart .px{{fill:none;stroke:var(--accent);stroke-width:2}}.chart .vw{{fill:none;stroke:var(--warn);stroke-width:1.2;stroke-dasharray:5 4}}
.chart .or{{stroke:var(--violet);stroke-dasharray:2 4}}.chart .mb{{fill:var(--up)}}.chart .ms{{fill:var(--down)}}
.chart .ml{{fill:var(--ink);font-size:10px;font-weight:700}}.chart .d{{fill:var(--muted);opacity:.6}}
.chart .f90{{fill:rgba(144,133,233,.12)}}.chart .f50{{fill:rgba(144,133,233,.25)}}.chart .fmed{{fill:none;stroke:var(--violet);stroke-width:1.2;stroke-dasharray:4 3}}
.chart .tw{{stroke:var(--up);stroke-width:1.5;stroke-dasharray:3 3}}.chart .tl{{stroke:var(--down);stroke-width:1.5;stroke-dasharray:3 3}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;font-size:11.5px;color:var(--muted);margin-top:6px}}
.legend i{{display:inline-block;width:14px;height:3px;margin-right:5px;vertical-align:middle}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-bottom:14px}}
.ch{{display:flex;justify-content:space-between;margin-bottom:6px}}.kv{{font-size:12.5px;color:var(--ink-2)}}.kv span{{color:var(--muted);display:inline-block;width:92px}}
.lbl{{color:var(--muted)}}.card p{{margin:8px 0 0;font-size:12.5px}}
.view{{font-style:italic;color:var(--ink-2);margin:0}}
.tw-table{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th,td{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--muted);font-weight:600}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}tr.buy b{{color:var(--up)}}tr.short b,tr.sell b{{color:var(--down)}}tr.hold b{{color:var(--muted)}}
footer{{color:var(--muted);font-size:11.5px;margin-top:18px}}
</style></head><body><div class="wrap">
<header><div><div class="brand">Breaking Money · Time travel</div><h1>{_e(symbol)} · {day:%a %d %b %Y}</h1>
<div class="sub">{who} replayed this day from 09:15, seeing only the bars up to each moment. {lev}x intraday, paper only.</div></div>
<span class="badge">REPLAY · PAPER ONLY</span></header>
<div class="tiles">{tiles_html}</div>
<div class="panel"><h2>The day, bar by bar</h2>{_chart(day_bars, trades, decisions, fc)}
<div class="legend"><span><i style="background:var(--accent)"></i>{_e(symbol)} price (5 min)</span><span><i style="background:var(--warn)"></i>VWAP</span>
<span><i style="background:var(--violet)"></i>Opening range (09:15–09:45)</span><span>▲ buy · ▼ sell / short</span><span>· grey dots = HOLD calls</span>{'<span><i style="background:rgba(144,133,233,.45)"></i>10,000 simulated paths from entry (50% / 90% bands, median)</span>' if fc else ''}</div></div>
{_fc_panel(fc) if fc else ''}
{('<div class="cards">' + "".join(card(t) for t in trades) + "</div>") if trades else '<div class="panel muted">No trade taken: ' + _e(who) + ' did not find a setup worth the risk on this day.</div>'}
<div class="panel"><h2>{_e(who)}'s last read of the market</h2>{view_html or '<p class="muted">—</p>'}</div>
<div class="panel"><h2>Every call, in order</h2><div class="tw-table"><table><tr><th>Time</th><th>Call</th><th>Conf.</th><th>Reason</th><th>Result</th></tr>{rows}</table></div></div>
<footer>Real NSE prices from Yahoo (5-minute bars). Fills include spread, slippage and Indian charges. Replay of a past day, not a live trade.</footer>
</div></body></html>"""
