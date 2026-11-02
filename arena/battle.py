"""Claude vs Gemini: two AIs, ₹1 lakh each, the same replayed market days.

Fairness: both corners get the same bars (replayed with no lookahead), the same
starting capital, the same config (profile, risk limits, charges, slippage),
the same system prompt and the same JSON decision contract. Neither gets the
rules fallback, so a failed call simply skips that corner's cycle.

Two modes:
- real (default): Claude (your claude-cli login or API key) vs Gemini (GEMINI_API_KEY).
- demo: rule-bot stand-ins play both corners. The page is stamped
  "ILLUSTRATIVE DEMO" and must not be presented as what Claude or Gemini made.

Stock names never appear on the page: each stock gets a snack codename.
Paper only. Nothing here can place an order.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import logging
import random
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .clock import IST
from .config import PROFILES, ROOT, apply_profile
from .db import Journal
from . import insights as ins

log = logging.getLogger("arena")

FIGHTERS = {
    "claude": {"name": "Claude", "emoji": "🧡", "color": "#e07a53", "glow": "rgba(224,122,83,.45)",
               "nick": "The Zen Monk of Margin", "from": "San Francisco, by way of a very calm library",
               "move": "The Patient Stare", "walkout": "walks out slowly, sips filter kaapi, reads the whole chart twice"},
    "gemini": {"name": "Gemini", "emoji": "✨", "color": "#5b8def", "glow": "rgba(91,141,239,.45)",
               "nick": "The Twin-Turbo Hype Machine", "from": "Mountain View, by way of 47 browser tabs",
               "move": "The Double Take", "walkout": "sprints in, high-fives itself (it is twins), checks every stock at once"},
}
CODENAMES = ["Biryani 🍛", "Filter Kaapi ☕", "Masala Dosa 🥞", "Medu Vada 🍩", "Kothu Parotta 🥙",
             "Idli 🍚", "Murukku 🥨", "Payasam 🍮", "Bajji 🌶️", "Jigarthanda 🥤", "Pongal 🍲", "Sundal 🫘"]
# Company names the AIs might write in their reasons, so the page never leaks them.
COMPANY_NAMES = {
    "RELIANCE": ["Reliance Industries", "Reliance", "RIL"], "TCS": ["Tata Consultancy Services", "Tata Consultancy", "TCS"],
    "HDFCBANK": ["HDFC Bank", "HDFC"], "INFY": ["Infosys", "INFY"], "ICICIBANK": ["ICICI Bank", "ICICI"],
    "SBIN": ["State Bank of India", "State Bank", "SBI"], "BHARTIARTL": ["Bharti Airtel", "Airtel", "Bharti"],
    "ITC": ["ITC"], "LT": ["Larsen & Toubro", "Larsen", "L&T"], "AXISBANK": ["Axis Bank", "Axis"],
}
EXITS = {"ai": "its own exit call", "stop_loss": "stop-loss hit 🛑", "target": "target hit 🎯",
         "square_off": "3:15 PM auto-exit ⏰", "kill_switch": "daily loss limit 🚨"}


# ---- setup ----------------------------------------------------------------
class Scorekeeper:
    """Wraps a brain to count calls, errors and thinking time (all shown on the page)."""

    def __init__(self, brain):
        self.brain, self.name = brain, brain.name
        self.calls = self.errors = 0
        self.seconds = 0.0

    def decide(self, ctx: dict) -> dict:
        self.calls += 1
        t = time.time()
        try:
            return self.brain.decide(ctx)
        except Exception:
            self.errors += 1
            raise
        finally:
            self.seconds += time.time() - t

    def write(self, system: str, user: str) -> str:
        return self.brain.write(system, user)


def corner_cfg(cfg: dict, corner: str, demo: bool) -> dict:
    c = copy.deepcopy(cfg)
    st = c["strategy"]
    st["rules_fallback"] = False  # no free help from the rules bot in a fight
    if demo:
        # Stand-ins need different styles or the demo is a mirror match:
        # the Claude corner keeps your profile, the Gemini corner plays "aggressive".
        if corner == "gemini":
            for k in ("allow_short", "leverage", "min_trade_value", "profile_notes"):
                st.pop(k, None)
            st["profile"] = "aggressive" if cfg["strategy"].get("profile") != "aggressive" else "balanced"
            apply_profile(c)
        st["brain"] = "rules"
    elif corner == "claude":
        if not st["brain"].startswith("claude"):
            from .brain import find_claude
            import os
            st["brain"] = "claude-cli" if find_claude() else "claude-api" if os.environ.get("ANTHROPIC_API_KEY") else "claude-cli"
    else:
        import os
        from .brain import find_gemini
        st["brain"] = ("gemini-api" if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                       else "gemini-cli" if find_gemini() else "gemini-api")
    return c


def load_bars(cfg: dict, n_days: int, source: str = "auto", seed: int = 7) -> tuple[dict, list[date], str]:
    """Bars for the watchlist plus the last `n_days` trading days to fight on."""
    syms = cfg["market"]["watchlist"]
    bars, src = {}, ""
    if source in ("auto", "yahoo", "history"):
        from .feeds import load_history
        bars = load_history(cfg, syms) if source in ("auto", "history") else {}
        src = "saved NSE history"
        if not bars and source in ("auto", "yahoo"):
            try:
                from .feeds.yahoo import YahooFeed
                bars = YahooFeed().history(syms, period="60d", interval="5m")
                src = "real NSE prices (Yahoo 5-min bars)"
            except Exception as e:
                log.warning("Could not download real prices (%s).", e)
                bars = {}
        if not bars and source != "auto":
            raise RuntimeError("No real price history available. Run `python -m arena fetch` or use --source synthetic.")
    if not bars:
        from .clock import trading_days
        from .feeds.sim import synth_bars
        days = trading_days(date(2026, 10, 5), date(2026, 12, 31), set())[: n_days + 2]
        bars, src = synth_bars(syms, days, seed), "made-up practice prices (synthetic)"
    days = sorted(set().union(*[set(df.index.date) for df in bars.values()]))
    if len(days) < 2:
        raise RuntimeError("Need at least 2 days of bars (one to warm up indicators).")
    return bars, days[-n_days:] if len(days) > n_days else days[1:], src


def run(cfg: dict, days: int = 5, demo: bool = False, source: str = "auto", seed: int = 7,
        bars: dict | None = None, out: Path | None = None, brains: dict | None = None, on_runner=None) -> Path:
    """Fight! Returns the battle page path. `brains` lets tests inject fakes."""
    from .brain import RulesBrain, make_brain
    from .feeds.base import BarReplayFeed
    from .runner import Runner

    if bars is None:
        bars, fight_days, src = load_bars(cfg, days, source, seed)
    else:
        all_days = sorted(set().union(*[set(df.index.date) for df in bars.values()]))
        fight_days, src = all_days[-days:] if len(all_days) > days else all_days[1:], "given bars"
    out = Path(out or ROOT / "reports" / "battle.html")
    work = out.parent / "battle"
    work.mkdir(parents=True, exist_ok=True)
    db = Path(cfg["paths"]["data_dir"]) / "battle.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()  # one battle per page; old pages are archived below
    j = Journal(str(db))
    start = datetime.combine(fight_days[0], datetime.min.time(), IST)
    end = datetime.combine(fight_days[-1], datetime.max.time(), IST)
    corners = {}
    for key in ("claude", "gemini"):
        c = corner_cfg(cfg, key, demo)
        c["account"]["challenge_days"] = len(fight_days)
        c["paths"]["db"] = str(db)
        brain = (brains or {}).get(key) or (RulesBrain(c) if demo else make_brain(c))
        sk = Scorekeeper(brain)
        model = getattr(brain, "model", "") or c["strategy"].get("cli_model") or ""
        feed = BarReplayFeed(bars, start, end)
        rid = j.new_run(f"Battle · {FIGHTERS[key]['name']} corner", "battle", src, brain.name, model,
                        c["account"]["starting_capital"], c, feed.now().isoformat())
        log.info("🥊 %s corner: %s%s on %d day(s) of %s", FIGHTERS[key]["name"], brain.name,
                 f" ({model})" if model else "", len(fight_days), src)
        runner = Runner(c, feed, j, rid, brain=sk, report_path=work / f"{key}.html")
        if on_runner:
            on_runner(runner)  # lets the GUI's Stop button reach it
        runner.run()
        if runner.stop_requested:
            raise RuntimeError("Battle stopped before the final bell; no page written.")
        corners[key] = {"run_id": rid, "brain": brain.name, "model": model, "score": sk, "cfg": c}
    meta = {"demo": demo, "source": src, "days": [d.isoformat() for d in fight_days],
            "symbols": list(cfg["market"]["watchlist"]), "made": datetime.now(IST).strftime("%d %b %Y, %I:%M %p")}
    page = render(j, corners, meta)
    out.write_text(page, encoding="utf-8")
    (work / f"battle-{datetime.now(IST):%Y%m%d-%H%M%S}{'-demo' if demo else ''}.html").write_text(page, encoding="utf-8")
    log.info("Battle page: %s", out)
    return out


# ---- helpers ---------------------------------------------------------------
def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def inr(x: float, sign: bool = False, paise: bool = False) -> str:
    """Indian digit grouping: 1,04,512.30"""
    neg = x < 0
    whole, frac = f"{abs(x):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    if head:
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        whole = head + "," + tail
    s = "₹" + whole + (("." + frac) if paise else "")
    return (("+" if not neg else "−") if sign else ("−" if neg else "")) + s


def codenames(symbols: list[str], salt: str = "") -> dict[str, str]:
    rng = random.Random(hashlib.sha256((",".join(symbols) + salt).encode()).hexdigest())
    names = CODENAMES[:]
    rng.shuffle(names)
    return {s: names[i % len(names)] + ("" if i < len(names) else f" #{i // len(names) + 1}") for i, s in enumerate(symbols)}


def hide(text: str, alias: dict[str, str]) -> str:
    """Replace every ticker and company name with its codename."""
    pairs = []
    for sym, code in alias.items():
        pairs += [(n, code) for n in COMPANY_NAMES.get(sym, [])] + [(sym, code), (sym + ".NS", code)]
    for name, code in sorted(pairs, key=lambda p: -len(p[0])):
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", code, text, flags=re.I)
    return text


def _pick(options: list[str], *seed) -> str:
    h = int(hashlib.md5("|".join(map(str, seed)).encode()).hexdigest(), 16)
    return options[h % len(options)]


def corner_stats(j: Journal, run_id: int) -> dict:
    run = j.one("SELECT * FROM runs WHERE id=?", (run_id,))
    cap = run["starting_capital"]
    acct = j.one("SELECT * FROM account WHERE run_id=?", (run_id,))
    pos = j.q("SELECT * FROM positions WHERE run_id=?", (run_id,))
    trades = j.q("SELECT * FROM trades WHERE run_id=? ORDER BY id", (run_id,))
    days = j.q("SELECT * FROM days WHERE run_id=? ORDER BY date", (run_id,))
    eq = j.q("SELECT ts, equity FROM equity WHERE run_id=? ORDER BY ts", (run_id,))
    dec = j.one("SELECT COUNT(*) n, SUM(action='HOLD') holds, SUM(action IN ('BUY','SHORT')) entries FROM decisions WHERE run_id=?", (run_id,))
    rejected = j.one("SELECT COUNT(*) n FROM orders WHERE run_id=? AND status='rejected'", (run_id,))["n"]
    final = acct["cash"] + sum(p["qty"] * (p["last_price"] or p["avg_price"]) for p in pos)
    ts, es = ins.trade_stats(trades), ins.equity_stats(eq, cap)
    pnl = final - cap
    return {"cap": cap, "final": final, "pnl": pnl, "pct": pnl / cap * 100, "trades": trades, "days": days, "eq": eq,
            "ts": ts, "dd": es["max_dd_pct"], "peak": es["peak"], "charges": acct["charges"], "slippage": acct["slippage"],
            "decisions": dec["n"] or 0, "holds": dec["holds"] or 0, "entries": dec["entries"] or 0, "rejected": rejected,
            "shorts": sum(1 for t in trades if t["qty"] < 0)}


# ---- charts ----------------------------------------------------------------
def _thin(rows: list[dict], n: int = 360) -> list[dict]:
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)] + [rows[-1]]


def pnl_chart(series: dict[str, list[dict]], cap: float, day_labels: list[str], W: int = 900, H: int = 300) -> str:
    """P&L lines on a shared trading-time axis (overnight gaps removed). series: corner -> equity rows."""
    L, R, T, B = 70, 16, 18, 30
    keys = [k for k in series if series[k]]
    if not keys:
        return '<div class="empty">No equity snapshots yet.</div>'
    pts = {k: _thin(series[k]) for k in keys}
    vals = [r["equity"] - cap for k in keys for r in pts[k]] + [0]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or 500
    lo, hi = lo - pad, hi + pad
    y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="P&L over the battle">']
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                   f'<text x="{L - 8}" y="{y(v) + 4:.1f}" class="ax" text-anchor="end">{inr(v, sign=True)}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="zero"/>')
    # x by day index + intraday fraction so every round gets equal width
    nd = max(len(day_labels), 1)
    span = (W - L - R) / nd
    for i, lab in enumerate(day_labels):
        x0 = L + i * span
        if i:
            out.append(f'<line x1="{x0:.1f}" x2="{x0:.1f}" y1="{T}" y2="{H - B}" class="sep"/>')
        out.append(f'<text x="{x0 + span / 2:.1f}" y="{H - 8}" class="ax" text-anchor="middle">{_e(lab)}</text>')

    def x_of(ts: str) -> float | None:
        t = datetime.fromisoformat(ts)
        d = t.date().isoformat()
        if d not in day_index:
            return None
        frac = min(max(((t.hour * 60 + t.minute) - 555) / 375, 0), 1)  # 09:15 → 15:30
        return L + (day_index[d] + frac) * span

    day_index = {d: i for i, d in enumerate(_chart_days)}
    for k in keys:
        f = FIGHTERS[k]
        xy = [(x_of(r["ts"]), y(r["equity"] - cap)) for r in pts[k]]
        xy = [(a, b) for a, b in xy if a is not None]
        if not xy:
            continue
        line = " ".join(f"{a:.1f},{b:.1f}" for a, b in xy)
        out.append(f'<polyline points="{line}" fill="none" stroke="{f["color"]}" stroke-width="2.6" stroke-linejoin="round" '
                   f'style="filter:drop-shadow(0 0 6px {f["glow"]})"/>')
        a, b = xy[-1]
        out.append(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="5" fill="{f["color"]}"/>'
                   f'<text x="{min(a, W - R - 40):.1f}" y="{b - 10:.1f}" class="lbl" fill="{f["color"]}">{f["emoji"]}</text>')
    out.append("</svg>")
    return "".join(out)


_chart_days: list[str] = []  # set by render() before charts are drawn


# ---- commentary --------------------------------------------------------------
def round_story(n: int, d: dict, c: dict) -> str:
    """Goofy one-liners for a round. d/c: day rows for Claude/Gemini (may be None)."""
    cp, gp = (d or {}).get("pnl", 0) or 0, (c or {}).get("pnl", 0) or 0
    ct, gt = (d or {}).get("trades", 0) or 0, (c or {}).get("trades", 0) or 0
    if ct == 0 and gt == 0:
        return _pick(["Both fighters stared at the chart like it was a Monday-morning standup. Zero trades. Pure meditation. 🧘",
                      "Nobody threw a punch. The referee checked if the AIs were still plugged in. They were. Just picky. 🔌",
                      "A full round of HOLD HOLD HOLD. The crowd started a Mexican wave out of boredom. 🌊"], n)
    lines = []
    for key, p, t in (("claude", cp, ct), ("gemini", gp, gt)):
        nm = FIGHTERS[key]["name"]
        if t == 0:
            lines.append(_pick([f"{nm} sat this one out, arms folded, judging everyone. 🙅",
                                f"{nm} said “not today” and went for a tea break. ☕",
                                f"{nm} kept its gloves on and its powder dry. 🧤"], n, key))
        elif t >= 3:
            mood = "and it paid off" if p > 0 else "and the charges ate the lunch" if p > -300 else "and most of them missed"
            lines.append(_pick([f"{nm} fired {t} trades, {mood}. Buffet mode activated. 🍽️",
                                f"{nm} took {t} swings, {mood}. Somebody hide the keyboard. ⌨️",
                                f"{nm} clicked {t} times like it was a Flipkart sale, {mood}. 🛒",
                                f"{nm} went {t} rounds of in-and-out, {mood}. Dizzy yet? 🌀"], n, key, t))
        elif p > 0:
            lines.append(_pick([f"{nm} landed a clean hit for {inr(p, sign=True)}. Crowd goes “sema!” 🔥",
                                f"{nm} walked away {inr(p, sign=True)} richer and did a little dance. 💃",
                                f"{nm} banked {inr(p, sign=True)}. Smooth like Mysore Pak. 🍬"], n, key))
        else:
            lines.append(_pick([f"{nm} took a punch: {inr(p, sign=True)}. Ouch, that one stung. 🤕",
                                f"{nm} slipped on a banana peel for {inr(p, sign=True)}. 🍌",
                                f"{nm} lost {inr(-p)} and is now staring at the wall. 🧱"], n, key))
    if cp != gp:
        w = "claude" if cp > gp else "gemini"
        lines.append(f"Round {n} goes to <b style=\"color:{FIGHTERS[w]['color']}\">{FIGHTERS[w]['name']}</b> "
                     f"by {inr(abs(cp - gp))}.")
    else:
        lines.append(f"Round {n} is a dead heat. 🤝")
    return " ".join(lines)


# ---- page --------------------------------------------------------------------
def render(j: Journal, corners: dict, meta: dict) -> str:
    global _chart_days
    _chart_days = meta["days"]
    alias = codenames(meta["symbols"], ",".join(meta["days"]))
    S = {k: corner_stats(j, v["run_id"]) for k, v in corners.items()}
    cap = S["claude"]["cap"]
    labels = [f"R{i + 1}" for i in range(len(meta["days"]))]
    c, g = S["claude"], S["gemini"]
    if abs(c["final"] - g["final"]) < 0.005:
        winner = None
    else:
        winner = "claude" if c["final"] > g["final"] else "gemini"
    loser = None if not winner else ("gemini" if winner == "claude" else "claude")
    pain = {k: S[k]["pct"] / max(S[k]["dd"], 0.25) for k in S}
    calm = min(S, key=lambda k: (S[k]["dd"], -S[k]["pnl"]))
    risk_king = max(pain, key=pain.get)
    demo = meta["demo"]
    dfrom = date.fromisoformat(meta["days"][0]).strftime("%d %b")
    dto = date.fromisoformat(meta["days"][-1]).strftime("%d %b %Y")

    tag = ('<div class="ribbon demo">🟡 ILLUSTRATIVE DEMO · rule-bot stand-ins played both corners · '
           'these are NOT real Claude or Gemini results · paper money only</div>' if demo else
           '<div class="ribbon real">🟢 REAL HEAD-TO-HEAD · Claude and Gemini each made every call on the same replayed market days · '
           'paper money only</div>')

    def model_line(k):
        v = corners[k]
        if demo:
            return f"stand-in: rules bot ({v['cfg']['strategy']['profile']} style)"
        return f"{v['brain']}" + (f" · {v['model']}" if v["model"] else " · default model")

    def card(k):
        f, s, sc = FIGHTERS[k], S[k], corners[k]["score"]
        hp = max(4, min(100, 50 + s["pct"] * 5))  # ±10% fills / empties the bar
        best, worst = s["ts"]["best"], s["ts"]["worst"]
        crown = "🏆 " if k == winner else ""
        return f"""
<section class="fighter" id="{k}" style="--c:{f['color']};--g:{f['glow']}">
  <div class="fhead"><div class="avatar">{f['emoji']}</div>
    <div><div class="fname">{crown}{f['name']}</div><div class="nick">“{f['nick']}”</div>
    <div class="muted small">Hometown: {f['from']} · Signature move: {f['move']}</div>
    <div class="muted small">Brain: {_e(model_line(k))}</div></div></div>
  <div class="hp"><div class="hpbar" style="width:{hp:.0f}%"></div><span>HP {hp:.0f}</span></div>
  <div class="big secret" data-reveal="{k}"><div class="lab">Final portfolio value</div>
    <div class="val count" data-to="{s['final']:.2f}">{inr(s['final'], paise=True)}</div>
    <div class="pnl {'up' if s['pnl'] >= 0 else 'down'}">{inr(s['pnl'], sign=True, paise=True)} <small>({s['pct']:+.2f}%)</small></div></div>
  <div class="tiles">
    <div><b>{s['ts']['trades']}</b><span>trades</span></div>
    <div><b>{s['ts']['win_rate']:.0f}%</b><span>win rate</span></div>
    <div><b>{s['dd']:.2f}%</b><span>max drawdown</span></div>
    <div><b>{inr(s['charges'])}</b><span>charges paid</span></div>
    <div><b>{s['holds']}</b><span>times it said “chill” (HOLD)</span></div>
    <div><b>{s['shorts']}</b><span>short sells</span></div>
    <div><b>{sc.seconds / max(sc.calls, 1):.1f}s</b><span>avg thinking time</span></div>
    <div><b>{sc.errors}</b><span>brain-freezes (skipped calls)</span></div>
  </div>
  <div class="chartbox"><div class="lab">{f['name']}'s P&L graph</div>{pnl_chart({k: s['eq']}, cap, labels, 900, 240)}</div>
  <div class="moves">
    <div><span class="lab">💥 Best punch</span>{_trade_line(best, alias) if best else '<span class="muted">No trades, no punches.</span>'}</div>
    <div><span class="lab">🤕 Worst bruise</span>{_trade_line(worst, alias) if worst and len(s['trades']) > 1 else '<span class="muted">Not enough trades to get bruised.</span>'}</div>
  </div>
</section>"""

    rounds = []
    dmap = {k: {d["date"]: d for d in S[k]["days"]} for k in S}
    for i, d in enumerate(meta["days"]):
        a, b = dmap["claude"].get(d), dmap["gemini"].get(d)
        ap, bp = (a or {}).get("pnl", 0) or 0, (b or {}).get("pnl", 0) or 0
        rw = "claude" if ap > bp else "gemini" if bp > ap else None
        rounds.append(f"""<tr><td><b>Round {i + 1}</b><div class="muted small">{date.fromisoformat(d).strftime('%a %d %b')}</div></td>
<td class="{'up' if ap > 0 else 'down' if ap < 0 else ''} secret" data-reveal="claude">{inr(ap, sign=True)}<div class="muted small">{(a or {}).get('trades', 0)} trades</div></td>
<td class="{'up' if bp > 0 else 'down' if bp < 0 else ''} secret" data-reveal="gemini">{inr(bp, sign=True)}<div class="muted small">{(b or {}).get('trades', 0)} trades</div></td>
<td class="secret" data-reveal="winner">{FIGHTERS[rw]['emoji'] + ' ' + FIGHTERS[rw]['name'] if rw else '🤝 Draw'}</td>
<td class="story secret" data-reveal="winner">{round_story(i + 1, a, b)}</td></tr>""")

    log_rows = []
    for k in ("claude", "gemini"):
        for t in S[k]["trades"]:
            side = "SHORT" if t["qty"] < 0 else "LONG"
            mv = (t["exit_price"] / t["entry_price"] - 1) * 100 * (-1 if t["qty"] < 0 else 1)
            log_rows.append((t["entry_ts"], f"""<tr><td style="color:{FIGHTERS[k]['color']}">{FIGHTERS[k]['emoji']} {FIGHTERS[k]['name']}</td>
<td>{datetime.fromisoformat(t['entry_ts']).strftime('%d %b %H:%M')}</td><td>{_e(alias.get(t['symbol'], 'Mystery snack'))}</td><td>{side}</td>
<td>{mv:+.2f}%</td><td class="{'up' if t['net_pnl'] > 0 else 'down'}">{inr(t['net_pnl'], sign=True)}</td>
<td>{_e(EXITS.get(t['exit_reason'], t['exit_reason']))}</td><td class="reason">{_e(hide(t['entry_reason'] or '', alias))}</td></tr>"""))
    log_rows = [r for _, r in sorted(log_rows)]

    if winner:
        margin = abs(c["final"] - g["final"])
        verdict = (f"<div class='belt'>🏆</div><h2>{FIGHTERS[winner]['emoji']} {FIGHTERS[winner]['name'].upper()} WINS!</h2>"
                   f"<p>{FIGHTERS[winner]['name']} beat {FIGHTERS[loser]['name']} by <b>{inr(margin, paise=True)}</b> "
                   f"after charges, on the exact same days and the exact same ₹1 lakh.</p>")
    else:
        verdict = "<div class='belt'>🤝</div><h2>IT'S A DRAW!</h2><p>Same money, same moves, same ending. Spooky.</p>"
    risk_note = (f"<p class='insight'>⚖️ <b>Risk crown:</b> {FIGHTERS[risk_king]['name']} earned the most return per unit of pain "
                 f"(return ÷ max drawdown). Calmest ride: {FIGHTERS[calm]['name']} with a {S[calm]['dd']:.2f}% worst dip. "
                 + ("Same AI won both crowns, so this round it was not just luck." if risk_king == winner else
                    "Different AI from the money winner. Highest profit is not automatically the best strategy. Risk-um mukkiyam!")
                 + "</p>")

    script = f"""
<table class="script"><tr><th>Placeholder</th><th>Fill in</th></tr>
<tr><td>[CLAUDE VALUE]</td><td>{inr(c['final'], paise=True)}</td></tr>
<tr><td>[CLAUDE P&amp;L]</td><td>{inr(c['pnl'], sign=True, paise=True)} ({c['pct']:+.2f}%)</td></tr>
<tr><td>[GEMINI VALUE]</td><td>{inr(g['final'], paise=True)}</td></tr>
<tr><td>[GEMINI P&amp;L]</td><td>{inr(g['pnl'], sign=True, paise=True)} ({g['pct']:+.2f}%)</td></tr>
<tr><td>[WINNER]</td><td>{FIGHTERS[winner]['name'] if winner else 'Draw'}</td></tr></table>
<p class="muted small">{'⚠️ Illustrative demo numbers. Say “demo” or “example” on screen if you use them.' if demo else
   '✅ Real head-to-head numbers from this battle. Still paper money, so keep the “paper trading experiment” line.'}</p>"""

    return PAGE.format(
        tag=tag, dfrom=dfrom, dto=dto, n=len(meta["days"]), src=_e(meta["source"]), made=_e(meta["made"]),
        cap=inr(cap), cc=FIGHTERS["claude"]["color"], gc=FIGHTERS["gemini"]["color"],
        claude=card("claude"), gemini=card("gemini"),
        both=pnl_chart({"claude": c["eq"], "gemini": g["eq"]}, cap, labels),
        rounds="".join(rounds), verdict=verdict, risk=risk_note, script=script,
        log="".join(log_rows) or '<tr><td colspan="8" class="muted">Nobody traded. Historic levels of chill.</td></tr>',
        rules=rulebook(corners, meta, alias, demo), demo_cls="isdemo" if demo else "",
        watermark='<div class="watermark">ILLUSTRATIVE DEMO</div>' if demo else "",
        walk_c=FIGHTERS["claude"]["walkout"], walk_g=FIGHTERS["gemini"]["walkout"],
        mc=_e(model_line("claude")), mg=_e(model_line("gemini")),
    )


def _trade_line(t: dict, alias: dict) -> str:
    side = "short" if t["qty"] < 0 else "long"
    when = datetime.fromisoformat(t["entry_ts"]).strftime("%a %H:%M")
    return (f"<div><b class=\"{'up' if t['net_pnl'] > 0 else 'down'}\">{inr(t['net_pnl'], sign=True)}</b> on "
            f"<b>{_e(alias.get(t['symbol'], 'Mystery snack'))}</b> ({side}, in at {when}, out by {_e(EXITS.get(t['exit_reason'], t['exit_reason']))})"
            f"<div class=\"reason\">“{_e(hide(t['entry_reason'] or '', alias))}”</div></div>")


def rulebook(corners: dict, meta: dict, alias: dict, demo: bool) -> str:
    st = corners["claude"]["cfg"]["strategy"]
    ex = corners["claude"]["cfg"]["execution"]
    menu = "".join(f"<li>{_e(v)}</li>" for v in alias.values())
    who = ("<p><b>Demo disclaimer, in big letters:</b> in this demo the two corners were played by the Arena's built-in rules bot "
           f"in two styles (Claude corner: <i>{_e(corners['claude']['cfg']['strategy']['profile'])}</i>, Gemini corner: "
           f"<i>{_e(corners['gemini']['cfg']['strategy']['profile'])}</i>). No AI was harmed, or even asked. Run a real battle to "
           "get numbers you can say out loud.</p>" if demo else
           "<p>Every BUY, SHORT, SELL and HOLD on this page was decided by the AI in that corner. The rules bot was benched "
           "for both (no fallback), so if an AI froze, its turn was simply skipped and counted as a brain-freeze.</p>")
    return f"""
<h3>📜 Rule 1: Same wallet</h3><p>Each AI got exactly {inr(corners['claude']['cfg']['account']['starting_capital'])} of virtual money.
No top-ups, no loans from amma, no crypto side quests.</p>
<h3>📈 Rule 2: Same market, no time machine</h3><p>Both AIs replayed the same {len(meta['days'])} trading days
({_e(meta['source'])}) bar by bar. At every moment they could only see prices up to that minute. The future was locked in a
steel almirah. Nobody got the key.</p>
<h3>🧠 Rule 3: Same skills, same prompt</h3><p>Both got the identical system prompt, the identical market snapshot
(trend, RSI, VWAP, momentum, volume, opening range, their own positions and P&amp;L) and the identical answer format.
The trading skills: profile <b>{_e(st.get('profile'))}</b>, a call every {st['decision_minutes']} minutes, stop-loss and target on every trade,
max daily loss {st['max_daily_loss_pct']}%, no new trades after {_e(st['no_new_entries_after'])}, auto-exit at {_e(st['square_off_time'])}.</p>
<h3>🤖 Rule 4: The AI decides, the robot referee executes</h3><p>The AI only says <i>what</i> and <i>why</i>. A strict
paper-trading engine decides <i>how many shares</i>, checks cash, and fills the order at a slightly worse price
(half of a {ex['spread_bps']} bps spread + {ex['slippage_bps']} bps slippage + size impact), {ex.get('replay_latency_seconds', 60)} s after the call, because the market
does not wait for anyone to finish thinking.</p>
<h3>💸 Rule 5: Real Indian charges</h3><p>Brokerage, STT, exchange fees, SEBI fees, stamp duty and GST were deducted
from every trade, exactly like a real contract note. Profits on this page are <b>after</b> charges.</p>
<h3>🙈 Rule 6: Secret stocks</h3><p>Stock names are hidden so nobody runs off to copy trades. Tonight's snack menu: </p><ul class="menu">{menu}</ul>
<p class="muted small">Every ticker and company name in the AIs' reasons is swapped for its snack codename too.</p>
<h3>🏅 Rule 7: How the winner is chosen</h3><p>Highest final portfolio value after the last bell wins the belt. A separate
<b>Risk crown</b> goes to the best return per unit of pain (return ÷ max drawdown), because making money while
riding a roller coaster is not the same as making money calmly.</p>
<h3>🥊 Who fought</h3>{who}
<h3>🚫 Rule 8: Zero real money, ever</h3><p>This is a paper-trading experiment. The Arena has no order code, a network
allow-list that blocks every broker order URL, and it refuses to start if anyone tries to switch paper mode off.
Not investment advice. Past performance of an AI in a replay says nothing about tomorrow.</p>"""


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude vs Gemini · ₹1 Lakh Battle</title>
<style>
:root{{--bg:#0b0d12;--panel:#141822;--line:#252b3a;--ink:#eef0f5;--mut:#8b93a7;--up:#36d399;--down:#ff6b6b;--gold:#ffd166}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(1200px 600px at 20% -10%,#2a1a14 0,transparent 60%),radial-gradient(1200px 600px at 80% -10%,#142037 0,transparent 60%),var(--bg);
color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:16px}}
.ribbon{{padding:10px 14px;border-radius:10px;font-weight:700;font-size:13px;letter-spacing:.3px;text-align:center}}
.ribbon.demo{{background:#3a300c;color:#ffe08a;border:1px solid #806a1a}}.ribbon.real{{background:#0f3324;color:#9ff0c4;border:1px solid #1d6b48}}
.hero{{text-align:center;padding:26px 0 8px}}.hero h1{{margin:0;font-size:clamp(30px,6vw,62px);font-weight:900;letter-spacing:-1px;line-height:1.05}}
.hero h1 .c{{color:{cc};text-shadow:0 0 24px rgba(224,122,83,.5)}}.hero h1 .g{{color:{gc};text-shadow:0 0 24px rgba(91,141,239,.5)}}
.vs{{display:inline-block;margin:0 10px;padding:2px 14px;border-radius:999px;background:var(--gold);color:#111;font-size:.45em;vertical-align:middle;transform:rotate(-6deg)}}
.sub{{color:var(--mut);margin-top:8px}}
.tape{{display:grid;grid-template-columns:1fr auto 1fr;gap:10px;align-items:center;margin:18px 0;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px}}
.tape .mid{{text-align:center;color:var(--gold);font-weight:800}}.tape .l{{text-align:right}}.small{{font-size:12.5px}}.muted{{color:var(--mut)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.fighter{{background:var(--panel);border:1px solid var(--line);border-top:4px solid var(--c);border-radius:16px;padding:16px;box-shadow:0 0 40px -18px var(--g)}}
.fhead{{display:flex;gap:12px;align-items:center}}.avatar{{font-size:42px;width:64px;height:64px;display:grid;place-items:center;border-radius:50%;background:#0006;border:2px solid var(--c)}}
.fname{{font-size:26px;font-weight:900;color:var(--c)}}.nick{{font-style:italic}}
.hp{{position:relative;height:16px;background:#2a1216;border-radius:8px;margin:14px 0 6px;overflow:hidden;border:1px solid #0008}}
.hpbar{{height:100%;background:linear-gradient(90deg,var(--c),var(--gold));transition:width 1.4s}}.hp span{{position:absolute;right:8px;top:-1px;font-size:11px;font-weight:800}}
.big{{margin:10px 0}}.lab{{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.6px;display:block;margin-bottom:2px}}
.val{{font-size:clamp(28px,4.4vw,42px);font-weight:900;font-variant-numeric:tabular-nums}}.pnl{{font-size:20px;font-weight:800}}
.up{{color:var(--up)}}.down{{color:var(--down)}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:10px 0}}.tiles div{{background:#0d1018;border:1px solid var(--line);border-radius:10px;padding:8px}}
.tiles b{{display:block;font-size:17px}}.tiles span{{color:var(--mut);font-size:11.5px}}
.chartbox{{background:#0d1018;border:1px solid var(--line);border-radius:12px;padding:10px;margin-top:8px}}
.chart{{width:100%;height:auto;display:block}}.chart .grid{{stroke:#ffffff10}}.chart .zero{{stroke:#ffffff55;stroke-dasharray:4 4}}.chart .sep{{stroke:#ffffff18}}
.chart .ax{{fill:var(--mut);font-size:12px}}.fighter .chart .ax{{font-size:17px}}.chart .lbl{{font-size:16px}}
.moves{{display:grid;gap:8px;margin-top:10px}}.moves>div{{background:#0d1018;border:1px solid var(--line);border-radius:10px;padding:10px}}
.reason{{color:#b9bfd0;font-size:12.5px;margin-top:4px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px;margin-top:16px}}
h2{{margin:.2em 0}}h3{{margin:1.1em 0 .2em;font-size:16px}}
table{{width:100%;border-collapse:collapse}}td,th{{padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}}th{{color:var(--mut);font-size:12px;text-transform:uppercase}}
.story{{font-size:13.5px}}.verdict{{text-align:center;padding:26px 16px;border:2px solid var(--gold);background:linear-gradient(180deg,#2b2410,#141822)}}
.belt{{font-size:64px;animation:bob 1.6s ease-in-out infinite}}@keyframes bob{{50%{{transform:translateY(-8px) rotate(-6deg)}}}}
.insight{{background:#0d1018;border-left:4px solid var(--gold);padding:10px 12px;border-radius:8px;text-align:left}}
.script td:first-child{{font-family:ui-monospace,Consolas,monospace;color:var(--gold)}}
.menu{{columns:3;margin:6px 0}}.legend span{{margin-right:16px}}
.controls{{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin:12px 0}}
.controls button{{background:#1c2230;color:var(--ink);border:1px solid var(--line);padding:8px 14px;border-radius:999px;font-weight:700;cursor:pointer}}
.controls button:hover{{border-color:var(--gold)}}
body.reel .secret{{filter:blur(16px);user-select:none}}body.reel .secret.shown{{filter:none;transition:filter .8s}}
details summary{{cursor:pointer;font-weight:800}}.scroll{{overflow-x:auto}}
.foot{{color:var(--mut);font-size:12px;text-align:center;margin:20px 0}}
.isdemo .watermark{{position:fixed;bottom:10px;right:10px;background:#3a300c;color:#ffe08a;border:1px solid #806a1a;padding:4px 10px;border-radius:8px;font-size:11px;font-weight:800;z-index:9}}
@media (max-width:820px){{.grid2{{grid-template-columns:1fr}}.tiles{{grid-template-columns:repeat(2,1fr)}}.menu{{columns:2}}.tape{{grid-template-columns:1fr}}.tape .l{{text-align:left}}}}
</style></head><body class="{demo_cls}"><div class="wrap">
{tag}
<div class="hero"><h1><span class="c">CLAUDE</span><span class="vs">VS</span><span class="g">GEMINI</span></h1>
<div class="sub">The ₹1 Lakh AI Trading Smackdown · {n} rounds ({dfrom} to {dto}) · {cap} each · same skills · paper money</div></div>
<div class="controls"><button onclick="reel(true)">🎬 Reel mode (hide numbers)</button><button onclick="show('claude')">🧡 Reveal Claude</button>
<button onclick="show('gemini')">✨ Reveal Gemini</button><button onclick="show('winner')">🏆 Reveal winner</button><button onclick="reel(false)">👀 Show everything</button></div>
<div class="tape"><div class="l"><b style="color:{cc}">🧡 IN THE ORANGE CORNER</b><div class="small muted">{walk_c}</div><div class="small">{mc}</div></div>
<div class="mid">⚔️<br>TALE OF<br>THE TAPE</div>
<div><b style="color:{gc}">✨ IN THE BLUE CORNER</b><div class="small muted">{walk_g}</div><div class="small">{mg}</div></div></div>
<div class="grid2">{claude}{gemini}</div>
<div class="card"><h2>📈 Both P&amp;L graphs, face to face</h2><div class="legend small"><span style="color:{cc}">━ 🧡 Claude</span><span style="color:{gc}">━ ✨ Gemini</span><span class="muted">dashed line = break-even · R1, R2… = rounds (trading days)</span></div>
<div class="secret" data-reveal="winner">{both}</div></div>
<div class="card verdict secret" data-reveal="winner" id="verdict">{verdict}{risk}</div>
<div class="card"><h2>🔔 Round by round</h2><div class="scroll"><table><tr><th>Round</th><th style="color:{cc}">🧡 Claude</th><th style="color:{gc}">✨ Gemini</th><th>Round winner</th><th>Ringside commentary</th></tr>{rounds}</table></div></div>
<div class="card"><h2>🎙️ Numbers for your reel script</h2>{script}</div>
<div class="card"><details><summary>🧾 Full fight log (every trade, both corners)</summary><div class="scroll"><table>
<tr><th>Fighter</th><th>When</th><th>Secret stock</th><th>Side</th><th>Move</th><th>Net P&amp;L</th><th>Exit</th><th>Why the AI did it</th></tr>{log}</table></div></details></div>
<div class="card"><h2>📖 The Official Rulebook (a.k.a. how we kept it fair)</h2>{rules}</div>
<div class="foot">Breaking Money · AI Trading Arena · built {made} · prices: {src}<br>Paper trading only. Virtual money, modelled slippage and real Indian charges. No real orders are ever placed. Not investment advice.</div>
</div>{watermark}
<script>
function reel(on){{document.body.classList.toggle('reel',on);document.querySelectorAll('.secret').forEach(e=>e.classList.remove('shown'))}}
function show(k){{document.querySelectorAll('[data-reveal="'+k+'"]').forEach(e=>{{e.classList.add('shown');e.querySelectorAll('.count').forEach(count)}});
 const el=document.querySelector(k==='winner'?'#verdict':'#'+k);if(el)el.scrollIntoView({{behavior:'smooth',block:'center'}})}}
function count(el){{const to=parseFloat(el.dataset.to),t0=performance.now(),d=1600;
 const f=n=>{{const s=Math.abs(n).toFixed(2).split('.'),w=s[0];const h=w.slice(0,-3),t=w.slice(-3);return (n<0?'−':'')+'₹'+(h?h.replace(/\\B(?=(\\d{{2}})+(?!\\d))/g,',')+',':'')+t+'.'+s[1]}};
 (function step(now){{const p=Math.min((now-t0)/d,1),e=1-Math.pow(1-p,3);el.textContent=f(to*e);if(p<1)requestAnimationFrame(step)}})(t0)}}
if(location.hash==='#reel')reel(true);
</script></body></html>"""


def placeholder() -> str:
    return """<!doctype html><html><body style="background:#0b0d12;color:#8b93a7;font:15px system-ui;display:grid;place-items:center;height:95vh">
<div style="text-align:center;max-width:520px"><div style="font-size:54px">🥊</div><h2 style="color:#eef0f5">No battle yet</h2>
<p>Press <b style="color:#ffd166">Build demo battle</b> for an illustrative preview (rule-bot stand-ins), or paste a Gemini key and press
<b style="color:#ffd166">Start real battle</b> to make Claude and Gemini trade the same days with ₹1 lakh each.</p></div></body></html>"""
