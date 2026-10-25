"""Daily P&L report: one downloadable page (and CSV) per trading day, documenting every trade."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .db import Journal
from .report import _cls, _e, _md, _rs


def run_days(j: Journal, run_id: int) -> list[str]:
    rows = j.q("SELECT DISTINCT substr(ts,1,10) d FROM decisions WHERE run_id=? "
               "UNION SELECT DISTINCT substr(exit_ts,1,10) FROM trades WHERE run_id=? "
               "UNION SELECT date FROM days WHERE run_id=? ORDER BY 1 DESC", (run_id, run_id, run_id))
    return [r["d"] for r in rows if r["d"]]


def _day_data(j: Journal, run_id: int, day: str) -> dict:
    run = j.one("SELECT * FROM runs WHERE id=?", (run_id,))
    cfg = json.loads(run["config_json"])
    row = j.one("SELECT * FROM days WHERE run_id=? AND date=?", (run_id, day))
    trades = j.q("SELECT * FROM trades WHERE run_id=? AND substr(exit_ts,1,10)=? ORDER BY exit_ts", (run_id, day))
    exits = {(o["symbol"], o["ts"]): o["note"] for o in j.q(
        "SELECT symbol, ts, note FROM orders WHERE run_id=? AND side='SELL' AND status='filled' AND substr(ts,1,10)=?", (run_id, day))}
    for t in trades:
        t["exit_note"] = exits.get((t["symbol"], t["exit_ts"]), "")
    calls = j.q("SELECT * FROM decisions WHERE run_id=? AND substr(ts,1,10)=? AND action!='HOLD' ORDER BY id", (run_id, day))
    views = j.q("SELECT ts, market_view FROM decisions WHERE run_id=? AND substr(ts,1,10)=? GROUP BY ts ORDER BY ts", (run_id, day))
    holds = j.one("SELECT COUNT(*) n FROM decisions WHERE run_id=? AND substr(ts,1,10)=? AND action='HOLD'", (run_id, day))["n"]
    eq = j.q("SELECT ts, equity FROM equity WHERE run_id=? AND substr(ts,1,10)=? ORDER BY ts", (run_id, day))
    prev = j.one("SELECT equity FROM equity WHERE run_id=? AND substr(ts,1,10)<? ORDER BY ts DESC LIMIT 1", (run_id, day))
    slip = j.one("SELECT COALESCE(SUM(slippage_cost),0) s FROM orders WHERE run_id=? AND substr(ts,1,10)=? AND status='filled'", (run_id, day))["s"]
    review = j.one("SELECT * FROM notes WHERE run_id=? AND kind='ai_review' AND substr(ts,1,10)=? ORDER BY rowid DESC LIMIT 1", (run_id, day))
    if row:
        start, end, insights, day_no = row["start_equity"], row["end_equity"], json.loads(row["insights"] or "[]"), row["day_no"]
    else:  # day still in progress
        start = prev["equity"] if prev else run["starting_capital"]
        end = eq[-1]["equity"] if eq else start
        insights = ["Day still in progress. Final notes are written at market close."]
        day_no = j.one("SELECT COUNT(*) n FROM days WHERE run_id=?", (run_id,))["n"] + 1
    return dict(run=run, cfg=cfg, day=day, day_no=day_no, start=start, end=end, pnl=end - start,
                total=end - run["starting_capital"], trades=trades, calls=calls, views=views, holds=holds,
                slippage=slip, insights=insights, review=review, final=bool(row))


def day_csv(j: Journal, run_id: int, day: str) -> str:
    d = _day_data(j, run_id, day)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "symbol", "qty", "entry_time", "entry_price", "exit_time", "exit_price", "trade_value",
                "gross_pnl", "charges", "net_pnl", "exit_reason", "why_claude_bought", "exit_detail"])
    for t in d["trades"]:
        w.writerow([day, t["symbol"], t["qty"], t["entry_ts"][11:19], t["entry_price"], t["exit_ts"][11:19], t["exit_price"],
                    round(t["qty"] * t["entry_price"], 2), t["gross_pnl"], t["charges"], t["net_pnl"], t["exit_reason"],
                    t["entry_reason"], t["exit_note"]])
    return buf.getvalue()


def day_html(j: Journal, run_id: int, day: str) -> str:
    d = _day_data(j, run_id, day)
    run, st = d["run"], d["cfg"]["strategy"]
    trades = d["trades"]
    wins = sum(t["net_pnl"] > 0 for t in trades)
    charges = sum(t["charges"] for t in trades)
    gross = sum(t["gross_pnl"] for t in trades)
    tiles = [
        ("Day P&L (net)", f'<span class="{_cls(d["pnl"])}">{_rs(d["pnl"], True)}</span>', f'{d["pnl"] / d["start"] * 100:+.2f}% on the day'),
        ("Portfolio value", _rs(d["end"]), f'started the day at {_rs(d["start"])}'),
        ("Total since start", f'<span class="{_cls(d["total"])}">{_rs(d["total"], True)}</span>', f'from {_rs(run["starting_capital"])}'),
        ("Trades", str(len(trades)), f"{wins} won · {len(trades) - wins} lost"),
        ("Gross P&L", _rs(gross, True), "before charges"),
        ("Charges paid", _rs(charges), f"plus slippage {_rs(d['slippage'])}"),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{s}</div></div>' for k, v, s in tiles)

    who = "Claude" if run["brain"].startswith("claude") else "the agent"
    exit_names = {"ai": f"{who}'s sell call", "stop_loss": "stop-loss hit", "target": "target hit",
                  "square_off": "3:15 PM auto-exit", "kill_switch": "daily loss limit"}

    def trade_block(i, t):
        hold = f'{t["hold_minutes"]:.0f} min'
        return (f'<div class="trade"><div class="th"><b>#{i} {_e(t["symbol"])}</b>'
                f'<span class="{_cls(t["net_pnl"])}">{_rs(t["net_pnl"], True)}</span></div>'
                f'<div class="tg">'
                f'<div><span>Bought</span>{t["qty"]} @ ₹{t["entry_price"]:,.2f} at {t["entry_ts"][11:16]}</div>'
                f'<div><span>Sold</span>@ ₹{t["exit_price"]:,.2f} at {t["exit_ts"][11:16]} ({hold})</div>'
                f'<div><span>Trade size</span>{_rs(t["qty"] * t["entry_price"])}</div>'
                f'<div><span>Gross / charges</span>{_rs(t["gross_pnl"], True)} / {_rs(t["charges"])}</div></div>'
                f'<p><span class="lbl">Why {who} bought:</span> {_e(t["entry_reason"]) or "—"}</p>'
                f'<p><span class="lbl">Why it closed ({_e(exit_names.get(t["exit_reason"], t["exit_reason"]))}):</span> {_e(t["exit_note"]) or "—"}</p></div>')

    trades_html = "".join(trade_block(i + 1, t) for i, t in enumerate(trades)) or '<p class="muted">No trades closed this day. The agent stayed in cash.</p>'
    calls_html = "".join(
        f'<tr><td>{_e(c["ts"][11:16])}</td><td><b class="{"up" if c["action"] == "BUY" else "down"}">{c["action"]}</b></td><td>{_e(c["symbol"])}</td>'
        f'<td>{(c["confidence"] or 0):.2f}</td><td>{_e(c["reason"])}</td><td class="muted">{_e(c["outcome"])}</td></tr>' for c in d["calls"]
    ) or '<tr><td colspan="6" class="muted">No BUY or SELL calls.</td></tr>'
    seen, views = set(), []
    for v in d["views"]:
        if v["market_view"] and v["market_view"] not in seen:
            seen.add(v["market_view"])
            views.append(f'<li><b>{_e(v["ts"][11:16])}</b> {_e(v["market_view"])}</li>')
    review = f'<section><h2>Claude\'s coach review</h2><div class="md">{_md(d["review"]["text"])}</div></section>' if d["review"] else ""
    status = "" if d["final"] else '<div class="note">Day in progress. Numbers update until market close.</div>'

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Day {d['day_no']} P&amp;L · {day}</title><style>
:root{{--ink:#14161b;--ink2:#4a4d55;--muted:#8a8c93;--line:#e3e4e8;--bg:#ffffff;--soft:#f5f6f8;--up:#0a7f0a;--down:#c42f2f;--accent:#2a6fd1}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--soft);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
.page{{max-width:900px;margin:0 auto;padding:28px 18px 50px;background:var(--bg)}}
.brand{{font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}}
h1{{margin:4px 0 2px;font-size:24px}}.sub{{color:var(--muted);font-size:12.5px}}
.badge{{display:inline-block;border:1px solid #e8b64a;color:#9a6a00;border-radius:999px;padding:2px 9px;font-size:10.5px;font-weight:700;letter-spacing:.06em;margin-left:6px}}
.tiles{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:18px 0}}
.tile{{border:1px solid var(--line);border-radius:10px;padding:10px 12px}}.k{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}}
.v{{font-size:20px;font-weight:800;margin:2px 0}}.s{{font-size:11.5px;color:var(--muted)}}
h2{{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink2);border-bottom:2px solid var(--line);padding-bottom:6px;margin:26px 0 12px}}
.up{{color:var(--up)}}.down{{color:var(--down)}}.muted{{color:var(--muted)}}
.trade{{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px;break-inside:avoid}}
.th{{display:flex;justify-content:space-between;font-size:15px}}.th span{{font-weight:800}}
.tg{{display:grid;grid-template-columns:1fr 1fr;gap:4px 16px;margin:8px 0;font-size:13px}}.tg span{{display:block;font-size:10.5px;color:var(--muted);text-transform:uppercase}}
.trade p{{margin:6px 0 0;font-size:13px;color:var(--ink2)}}.lbl{{font-weight:700;color:var(--ink)}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}th,td{{text-align:left;padding:6px;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--muted);font-weight:600}}
ul{{padding-left:18px}}li{{margin:4px 0}}.note{{background:#fff7e0;border:1px solid #f0d58a;border-radius:8px;padding:8px 12px;margin-top:12px;font-size:13px}}
.md h3{{font-size:14px;margin:12px 0 4px;color:var(--accent)}}footer{{margin-top:30px;color:var(--muted);font-size:11px;text-align:center}}
.print{{float:right;font:inherit;font-weight:700;border:1px solid var(--line);background:var(--soft);border-radius:8px;padding:6px 12px;cursor:pointer}}
@media (max-width:640px){{.tiles{{grid-template-columns:1fr 1fr}}.tg{{grid-template-columns:1fr}}}}
@media print{{body{{background:#fff}}.print{{display:none}}.page{{padding:0}}}}
</style></head><body><div class="page">
<button class="print" onclick="print()">Save as PDF</button>
<div class="brand">Breaking Money · AI Trading Arena</div>
<h1>Day {d['day_no']} P&amp;L report <span class="badge">PAPER TRADING</span></h1>
<div class="sub">{_e(day)} · {_e(run['name'])} · profile: {_e(st.get('profile', 'balanced'))} · decisions by: {_e(run['brain'])}</div>
{status}
<div class="tiles">{tiles_html}</div>
<h2>Key takeaways</h2><ul>{''.join(f'<li>{_e(n)}</li>' for n in d['insights'])}</ul>
{review}
<h2>Trade journal ({len(trades)})</h2>{trades_html}
<h2>Every BUY / SELL call Claude made</h2><table><tr><th>Time</th><th>Call</th><th>Stock</th><th>Conf.</th><th>Claude's reasoning</th><th>Result</th></tr>{calls_html}</table>
<p class="muted" style="font-size:12px">Plus {d['holds']} HOLD decisions (Claude chose not to trade).</p>
<h2>Claude's market view through the day</h2><ul>{''.join(views) or '<li class="muted">No views recorded.</li>'}</ul>
<footer>Simulated trades with virtual money on real prices, including slippage and Indian charges. No real orders were placed. Not investment advice.</footer>
</div></body></html>"""


def save_day(j: Journal, run_id: int, day: str, folder: str | Path) -> Path:
    out = Path(folder) / f"day-{day}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(day_html(j, run_id, day), encoding="utf-8")
    (out.with_suffix(".csv")).write_text(day_csv(j, run_id, day), encoding="utf-8")
    return out
