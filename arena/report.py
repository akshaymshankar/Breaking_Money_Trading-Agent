"""One-page results report: P&L, analytics, insights, trades and Claude's calls.

Writes a single self-contained HTML file (no internet needed to open it).
While a live run is active the page reloads itself every minute.
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

from . import insights as ins
from .db import Journal


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _rs(x: float, sign: bool = False) -> str:
    s = f"{abs(x):,.2f}"
    if sign:
        return ("+" if x >= 0 else "−") + "₹" + s
    return ("−" if x < 0 else "") + "₹" + s


def _cls(x: float) -> str:
    return "up" if x > 0 else "down" if x < 0 else ""


def write(j: Journal, run_id: int, path: str | Path, live: bool = False) -> Path:
    run = j.one("SELECT * FROM runs WHERE id=?", (run_id,))
    cfg = json.loads(run["config_json"])
    cap = run["starting_capital"]
    acct = j.one("SELECT * FROM account WHERE run_id=?", (run_id,))
    positions = j.q("SELECT * FROM positions WHERE run_id=?", (run_id,))
    trades = j.q("SELECT * FROM trades WHERE run_id=? ORDER BY id", (run_id,))
    days = j.q("SELECT * FROM days WHERE run_id=? ORDER BY date", (run_id,))
    eq = j.q("SELECT ts, equity FROM equity WHERE run_id=? ORDER BY ts", (run_id,))
    calls = j.q("SELECT * FROM decisions WHERE run_id=? AND action!='HOLD' ORDER BY id DESC LIMIT 200", (run_id,))
    nholds = j.one("SELECT COUNT(*) n FROM decisions WHERE run_id=? AND action='HOLD'", (run_id,))["n"]
    last_view = j.one("SELECT ts, market_view FROM decisions WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,))
    review = j.one("SELECT * FROM notes WHERE run_id=? AND kind='ai_review' ORDER BY rowid DESC LIMIT 1", (run_id,))
    others = j.q("SELECT r.id, r.name, r.brain, r.mode, r.starting_capital, a.cash, a.realized, a.charges, r.status FROM runs r JOIN account a ON a.run_id=r.id ORDER BY r.id DESC LIMIT 12")

    holdings = sum(p["qty"] * (p["last_price"] or p["avg_price"]) for p in positions)
    equity = acct["cash"] + holdings
    pnl = equity - cap
    ts = ins.trade_stats(trades)
    es = ins.equity_stats(eq, cap)
    sharpe = ins.daily_sharpe(days)
    target_days = cfg["account"]["challenge_days"]
    active = run["status"] == "active"

    pf = "∞" if ts["profit_factor"] == math.inf else f"{ts['profit_factor']:.2f}"
    tiles = [
        ("Win rate", f"{ts['win_rate']:.0f}%", f"{ts['wins']}W / {ts['losses']}L"),
        ("Profit factor", pf, "gross wins ÷ gross losses"),
        ("Max drawdown", f"{es['max_dd_pct']:.2f}%", "peak to trough"),
        ("Charges paid", _rs(acct["charges"]), f"slippage cost {_rs(acct['slippage'])}"),
        ("Trades", str(ts["trades"]), f"expectancy {_rs(ts['expectancy'], True)}/trade"),
        ("Sharpe (daily, ann.)", f"{sharpe:.2f}", f"{len(days)} day(s) of data"),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{s}</div></div>' for k, v, s in tiles)

    day_cells = []
    for i in range(target_days):
        if i < len(days):
            d = days[i]
            day_cells.append(f'<div class="day {_cls(d["pnl"])}" title="{_e(d["date"])}"><b>Day {i + 1}</b>'
                             f'<span>{_rs(d["pnl"], True)}</span><small>{d["trades"]} trades</small></div>')
        else:
            cur = " current" if i == len(days) and active else ""
            day_cells.append(f'<div class="day todo{cur}"><b>Day {i + 1}</b><span>{"LIVE" if cur else "—"}</span><small>&nbsp;</small></div>')

    pos_rows = "".join(
        f'<tr><td>{_e(p["symbol"])}</td><td class="n">{p["qty"]}</td><td class="n">{p["avg_price"]:.2f}</td>'
        f'<td class="n">{(p["last_price"] or 0):.2f}</td><td class="n {_cls(p["last_price"] - p["avg_price"])}">'
        f'{_rs((p["last_price"] - p["avg_price"]) * p["qty"], True)}</td><td class="n">{p["stop"]:.2f}</td><td class="n">{p["target"]:.2f}</td>'
        f'<td class="r">{_e(p["entry_reason"])}</td></tr>' for p in positions
    ) or '<tr><td colspan="8" class="muted">Flat. No open positions.</td></tr>'

    def grp_rows(g: dict) -> str:
        return "".join(
            f'<tr><td>{_e(k)}</td><td class="n">{v["trades"]}</td><td class="n">{v["win_rate"]:.0f}%</td>'
            f'<td class="n {_cls(v["net"])}">{_rs(v["net"], True)}</td></tr>'
            for k, v in sorted(g.items(), key=lambda kv: -kv[1]["net"])
        ) or '<tr><td colspan="4" class="muted">No closed trades yet.</td></tr>'

    by_sym = grp_rows(ins.group(trades, lambda t: t["symbol"]))
    by_exit = grp_rows(ins.group(trades, lambda t: t["exit_reason"].replace("_", " ")))

    def trade_row(t):
        return (f'<tr><td>{_e(t["exit_ts"][:10])}</td><td>{_e(t["symbol"])}</td><td class="n">{t["qty"]}</td>'
                f'<td class="n">{t["entry_price"]:.2f}</td><td class="n">{t["exit_price"]:.2f}</td>'
                f'<td class="n">{t["entry_ts"][11:16]}→{t["exit_ts"][11:16]}</td><td class="n">{t["charges"]:.2f}</td>'
                f'<td class="n {_cls(t["net_pnl"])}">{_rs(t["net_pnl"], True)}</td><td>{_e(t["exit_reason"].replace("_", " "))}</td></tr>')

    def call_row(c):
        return (f'<tr><td class="n">{_e(c["ts"][5:16].replace("T", " "))}</td><td><span class="act {c["action"].lower()}">{c["action"]}</span></td>'
                f'<td>{_e(c["symbol"])}</td><td class="n">{(c["confidence"] or 0):.2f}</td><td class="r">{_e(c["reason"])}</td>'
                f'<td class="r muted">{_e(c["outcome"])}</td></tr>')

    def split_rows(rows, fn, head, cols, empty, first=25):
        if not rows:
            return f'<table>{head}<tr><td colspan="{cols}" class="muted">{empty}</td></tr></table>'
        top = f'<table>{head}{"".join(fn(r) for r in rows[:first])}</table>'
        if len(rows) > first:
            top += (f'<details><summary class="muted">Show {len(rows) - first} more</summary>'
                    f'<table>{head}{"".join(fn(r) for r in rows[first:])}</table></details>')
        return top

    trade_rows = split_rows(list(reversed(trades)), trade_row,
                            "<tr><th>Date</th><th>Stock</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Time</th><th>Charges</th><th>Net P&amp;L</th><th>Exit reason</th></tr>",
                            9, "No closed trades yet.")
    call_rows = split_rows(calls, call_row,
                           "<tr><th>Time</th><th>Call</th><th>Stock</th><th>Conf.</th><th>Reason</th><th>Engine result</th></tr>",
                           6, "No BUY/SELL calls yet.", first=15)


    run_notes = ins.run_insights(j, run_id)
    insight_html = "".join(f"<li>{_e(n)}</li>" for n in run_notes)
    day_notes = "".join(
        f'<details{" open" if i == len(days) - 1 else ""}><summary><b>Day {d["day_no"]}</b> · {_e(d["date"])} · '
        f'<span class="{_cls(d["pnl"])}">{_rs(d["pnl"], True)}</span></summary><ul>'
        + "".join(f"<li>{_e(n)}</li>" for n in json.loads(d["insights"] or "[]")) + "</ul></details>"
        for i, d in enumerate(days)
    ) or '<p class="muted">Day notes appear after the first market close.</p>'

    review_html = ""
    if review:
        review_html = f'<section class="card wide"><h2>Claude\'s coach review <small>{_e(review["ts"][:16])}</small></h2><div class="md">{_md(review["text"])}</div></section>'

    board = "".join(
        f'<tr{" class=me" if o["id"] == run_id else ""}><td>#{o["id"]}</td><td>{_e(o["name"])}</td><td>{_e(o["brain"])}</td><td>{_e(o["mode"])}</td>'
        f'<td class="n {_cls(o["realized"])}">{_rs(o["realized"], True)}</td><td class="n">{o["realized"] / o["starting_capital"] * 100:+.2f}%</td>'
        f'<td>{_e(o["status"])}</td></tr>' for o in others
    )

    chart_data = {
        "equity": [[e["ts"][:16].replace("T", " "), e["equity"]] for e in eq],
        "capital": cap,
        "days": [[f"D{d['day_no']}", d["date"], d["pnl"]] for d in days],
    }

    page = TEMPLATE
    for k, v in {
        "{{REFRESH}}": '<meta http-equiv="refresh" content="60">' if (live and active) else "",
        "{{NAME}}": _e(run["name"]),
        "{{SUB}}": _e(f"Run #{run_id} · {run['mode']} · feed: {run['feed']} · brain: {run['brain']}"
                      + (f" ({run['model']})" if run["brain"] == "claude-api" else "") + f" · started {run['started_at'][:16].replace('T', ' ')}"),
        "{{STATUS}}": "LIVE" if active and live else run["status"].upper(),
        "{{STATUSCLS}}": "live" if active and live else "",
        "{{EQUITY}}": _rs(equity),
        "{{PNL}}": _rs(pnl, True),
        "{{PNLPCT}}": f"{pnl / cap * 100:+.2f}%",
        "{{PNLCLS}}": _cls(pnl),
        "{{CAP}}": _rs(cap),
        "{{CASH}}": _rs(acct["cash"]),
        "{{HOLD}}": _rs(holdings),
        "{{DAYNO}}": str(min(len(days) + (1 if active else 0), target_days)),
        "{{TARGET}}": str(target_days),
        "{{TILES}}": tiles_html,
        "{{DAYS}}": "".join(day_cells),
        "{{POS}}": pos_rows,
        "{{INSIGHTS}}": insight_html,
        "{{DAYNOTES}}": day_notes,
        "{{REVIEW}}": review_html,
        "{{BYSYM}}": by_sym,
        "{{BYEXIT}}": by_exit,
        "{{TRADES}}": trade_rows,
        "{{CALLS}}": call_rows,
        "{{NHOLDS}}": str(nholds),
        "{{VIEW}}": _e(last_view["market_view"]) if last_view else "No calls yet.",
        "{{VIEWTS}}": _e(last_view["ts"][:16].replace("T", " ")) if last_view else "",
        "{{BOARD}}": board,
        "{{DATA}}": json.dumps(chart_data).replace("</", "<\\/"),
    }.items():
        page = page.replace(k, v)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(page, encoding="utf-8")
    tmp.replace(out)
    return out


def _md(text: str) -> str:
    """Tiny Markdown subset: headings, bullets, bold, paragraphs."""
    import re
    out, in_list = [], False
    for line in text.splitlines():
        s = _e(line.strip())
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        if s.startswith(("- ", "* ")) or re.match(r"^\d+\. ", s):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>" + re.sub(r"^(- |\* |\d+\. )", "", s) + "</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if s.startswith("#"):
            out.append("<h3>" + s.lstrip("#").strip() + "</h3>")
        elif s:
            out.append("<p>" + s + "</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{{REFRESH}}
<title>AI Trading Arena</title>
<style>
:root{
  --page:#08090c; --surface:#12141a; --surface-2:#1a1d25; --line:#262a35; --axis:#383835;
  --ink:#f0efec; --ink-2:#c3c2b7; --muted:#898781;
  --accent:#3987e5; --accent-glow:rgba(57,135,229,.35);
  --up:#0ca30c; --down:#d03b3b; --warn:#fab219; --violet:#9085e9;
}
*{box-sizing:border-box} html,body{margin:0}
body{background:radial-gradient(1200px 600px at 20% -10%,#132036 0,transparent 60%),var(--page);color:var(--ink);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding:20px 16px 60px}
.wrap{max-width:1240px;margin:0 auto}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin-bottom:18px}
.brand{font-weight:800;letter-spacing:.14em;text-transform:uppercase;font-size:12px;color:var(--accent)}
h1{margin:2px 0 0;font-size:22px} .sub{color:var(--muted);font-size:12px}
.badges{display:flex;gap:8px;align-items:center}
.badge{border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-size:11px;font-weight:700;letter-spacing:.08em}
.badge.paper{color:var(--warn);border-color:rgba(250,178,25,.4)}
.badge.live{color:var(--up);border-color:rgba(12,163,12,.5)} .badge.live:before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--up);margin-right:6px;animation:p 1.4s infinite}
@keyframes p{50%{opacity:.25}}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}
.card{background:linear-gradient(180deg,var(--surface-2),var(--surface));border:1px solid var(--line);border-radius:14px;padding:16px;grid-column:span 6;min-width:0}
.card.wide{grid-column:1/-1} .card.third{grid-column:span 4}
h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2);margin:0 0 12px;display:flex;justify-content:space-between;gap:8px}
h2 small{color:var(--muted);font-weight:400;letter-spacing:0;text-transform:none}
.hero{display:flex;flex-wrap:wrap;gap:28px;align-items:flex-end}
.hero .big{font-size:40px;font-weight:800;line-height:1.05;text-shadow:0 0 24px var(--accent-glow)}
.hero .pnl{font-size:22px;font-weight:700}
.hero .meta{color:var(--muted);font-size:12px}
.up{color:var(--up)} .down{color:var(--down)} .muted{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:16px}
.tile{background:var(--page);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.tile .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.tile .v{font-size:22px;font-weight:700;margin:2px 0} .tile .s{color:var(--muted);font-size:11px}
.days{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px}
.day{border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:var(--page);display:flex;flex-direction:column;gap:2px}
.day b{font-size:11px;color:var(--muted);letter-spacing:.06em;text-transform:uppercase}
.day span{font-weight:700} .day small{color:var(--muted);font-size:11px}
.day.up{border-color:rgba(12,163,12,.55);box-shadow:inset 0 -3px 0 var(--up)} .day.up span{color:var(--up)}
.day.down{border-color:rgba(208,59,59,.55);box-shadow:inset 0 -3px 0 var(--down)} .day.down span{color:var(--down)}
.day.todo{opacity:.55} .day.current{opacity:1;border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),0 0 18px var(--accent-glow)}
.chart{position:relative;width:100%} .chart svg{display:block;width:100%;height:auto;overflow:visible}
.tip{position:absolute;pointer-events:none;background:#000c;border:1px solid var(--line);border-radius:8px;padding:6px 8px;font-size:12px;white-space:nowrap;transform:translate(-50%,-110%);display:none}
.tbl{overflow-x:auto} table{width:100%;border-collapse:collapse;font-size:12.5px}
th{color:var(--muted);font-weight:600;text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid #1d2029;vertical-align:top}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap} td.r{min-width:220px;color:var(--ink-2)}
tr.me td{background:rgba(57,135,229,.08)}
.act{font-size:10.5px;font-weight:800;padding:2px 7px;border-radius:6px;letter-spacing:.06em}
.act.buy{background:rgba(12,163,12,.16);color:var(--up)} .act.sell{background:rgba(208,59,59,.16);color:var(--down)}
ul{margin:0;padding-left:18px} li{margin:4px 0}
details{border-top:1px solid var(--line);padding:8px 0} summary{cursor:pointer}
.md h3{font-size:14px;margin:14px 0 6px;color:var(--accent)} .md p{margin:6px 0}
.view{border-left:3px solid var(--violet);padding:6px 12px;color:var(--ink-2);background:var(--page);border-radius:0 8px 8px 0}
footer{color:var(--muted);font-size:11px;margin-top:24px;text-align:center}
@media (max-width:860px){.card,.card.third{grid-column:1/-1}.hero .big{font-size:32px}}
</style></head><body><div class="wrap">
<header>
  <div><div class="brand">Breaking Money · AI Trading Arena</div><h1>{{NAME}}</h1><div class="sub">{{SUB}}</div></div>
  <div class="badges"><span class="badge paper">PAPER ONLY · VIRTUAL ₹</span><span class="badge {{STATUSCLS}}">{{STATUS}}</span></div>
</header>
<div class="grid">
<section class="card wide">
  <div class="hero">
    <div><div class="meta">Portfolio value</div><div class="big">{{EQUITY}}</div></div>
    <div><div class="meta">Net P&amp;L after charges</div><div class="pnl {{PNLCLS}}">{{PNL}} <small>({{PNLPCT}})</small></div></div>
    <div class="meta">Start {{CAP}}<br>Cash {{CASH}} · Invested {{HOLD}}</div>
    <div class="meta">Challenge<br><b style="color:var(--ink);font-size:18px">Day {{DAYNO}} / {{TARGET}}</b></div>
  </div>
  <div class="tiles">{{TILES}}</div>
</section>
<section class="card wide"><h2>{{TARGET}}-day challenge</h2><div class="days">{{DAYS}}</div></section>
<section class="card"><h2>Equity curve <small>dashed line = starting capital</small></h2><div class="chart" id="eq"></div></section>
<section class="card"><h2>Daily P&amp;L <small>net of charges</small></h2><div class="chart" id="dp"></div></section>
<section class="card wide"><h2>Key insights</h2><ul>{{INSIGHTS}}</ul></section>
{{REVIEW}}
<section class="card wide"><h2>Open positions</h2><div class="tbl"><table><tr><th>Stock</th><th>Qty</th><th>Avg</th><th>LTP</th><th>Unrealised</th><th>Stop</th><th>Target</th><th>Why Claude bought</th></tr>{{POS}}</table></div></section>
<section class="card wide"><h2>Claude's latest market view <small>{{VIEWTS}}</small></h2><div class="view">{{VIEW}}</div></section>
<section class="card"><h2>P&amp;L by stock</h2><div class="tbl"><table><tr><th>Stock</th><th>Trades</th><th>Win%</th><th>Net</th></tr>{{BYSYM}}</table></div></section>
<section class="card"><h2>P&amp;L by exit reason</h2><div class="tbl"><table><tr><th>Exit</th><th>Trades</th><th>Win%</th><th>Net</th></tr>{{BYEXIT}}</table></div></section>
<section class="card wide"><h2>Day-by-day notes</h2>{{DAYNOTES}}</section>
<section class="card wide"><h2>Claude's calls <small>latest BUY/SELL calls · {{NHOLDS}} HOLDs not shown</small></h2><div class="tbl">{{CALLS}}</div></section>
<section class="card wide"><h2>Trade log</h2><div class="tbl">{{TRADES}}</div></section>
<section class="card wide"><h2>Leaderboard <small>all runs in this database</small></h2><div class="tbl"><table><tr><th>Run</th><th>Name</th><th>Brain</th><th>Mode</th><th>Realised P&amp;L</th><th>Return</th><th>Status</th></tr>{{BOARD}}</table></div></section>
</div>
<footer>Simulation only. Virtual money, real prices, modelled slippage and Indian charges. No real orders are ever placed. Not investment advice.</footer>
</div>
<script>
const D={{DATA}};
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const NS="http://www.w3.org/2000/svg";
function el(t,a,p){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);p&&p.appendChild(e);return e}
const fmt=v=>(v<0?"−":"")+"₹"+Math.abs(v).toLocaleString("en-IN",{maximumFractionDigits:0});
function frame(host,W,H){const s=el("svg",{viewBox:`0 0 ${W} ${H}`,role:"img"},host);const tip=document.createElement("div");tip.className="tip";host.appendChild(tip);return[s,tip]}
function ticks(lo,hi,n){const st=Math.pow(10,Math.floor(Math.log10((hi-lo)/n||1)));const m=[1,2,2.5,5,10].map(x=>x*st).find(x=>(hi-lo)/x<=n)||st*10;const out=[];for(let v=Math.ceil(lo/m)*m;v<=hi+1e-9;v+=m)out.push(v);return out}
function line(id){const host=document.getElementById(id),pts=D.equity;if(pts.length<2){host.innerHTML='<p class="muted">The curve appears after a few snapshots.</p>';return}
 const W=600,H=260,L=64,R=10,T=10,B=26;const[s,tip]=frame(host,W,H);
 const ys=pts.map(p=>p[1]).concat([D.capital]);let lo=Math.min(...ys),hi=Math.max(...ys);const pad=(hi-lo)*.08||100;lo-=pad;hi+=pad;
 const x=i=>L+i*(W-L-R)/(pts.length-1),y=v=>T+(hi-v)*(H-T-B)/(hi-lo);
 for(const v of ticks(lo,hi,4)){el("line",{x1:L,x2:W-R,y1:y(v),y2:y(v),stroke:css("--line"),"stroke-width":1},s);const t=el("text",{x:L-8,y:y(v)+4,"text-anchor":"end","font-size":11,fill:css("--muted")},s);t.textContent=fmt(v)}
 el("line",{x1:L,x2:W-R,y1:y(D.capital),y2:y(D.capital),stroke:css("--muted"),"stroke-dasharray":"4 4"},s);
 const d=pts.map((p,i)=>(i?"L":"M")+x(i).toFixed(1)+","+y(p[1]).toFixed(1)).join("");
 const g=el("linearGradient",{id:"gq",x1:0,x2:0,y1:0,y2:1},el("defs",{},s));el("stop",{offset:0,"stop-color":css("--accent"),"stop-opacity":.35},g);el("stop",{offset:1,"stop-color":css("--accent"),"stop-opacity":0},g);
 el("path",{d:d+`L${x(pts.length-1)},${H-B}L${L},${H-B}Z`,fill:"url(#gq)"},s);
 el("path",{d,fill:"none",stroke:css("--accent"),"stroke-width":2,"stroke-linejoin":"round"},s);
 const first=pts[0][0].slice(0,10),last=pts[pts.length-1][0].slice(0,10);
 [[L,first,"start"],[W-R,last,"end"]].forEach(([xx,tx,a])=>{const t=el("text",{x:xx,y:H-6,"text-anchor":a,"font-size":11,fill:css("--muted")},s);t.textContent=tx});
 const cross=el("line",{y1:T,y2:H-B,stroke:css("--ink-2"),"stroke-width":1,visibility:"hidden"},s);const dot=el("circle",{r:4,fill:css("--accent"),stroke:css("--surface"),"stroke-width":2,visibility:"hidden"},s);
 const hit=el("rect",{x:L,y:T,width:W-L-R,height:H-T-B,fill:"transparent"},s);
 hit.addEventListener("pointermove",ev=>{const r=s.getBoundingClientRect(),px=(ev.clientX-r.left)*W/r.width;const i=Math.max(0,Math.min(pts.length-1,Math.round((px-L)/(W-L-R)*(pts.length-1))));
  cross.setAttribute("x1",x(i));cross.setAttribute("x2",x(i));cross.setAttribute("visibility","visible");dot.setAttribute("cx",x(i));dot.setAttribute("cy",y(pts[i][1]));dot.setAttribute("visibility","visible");
  tip.style.display="block";tip.style.left=(x(i)*r.width/W)+"px";tip.style.top=(y(pts[i][1])*r.height/H)+"px";const ch=pts[i][1]-D.capital;tip.innerHTML=`${pts[i][0]}<br><b>${fmt(pts[i][1])}</b> <span class="${ch>=0?"up":"down"}">${ch>=0?"+":""}${fmt(ch)}</span>`});
 hit.addEventListener("pointerleave",()=>{tip.style.display="none";cross.setAttribute("visibility","hidden");dot.setAttribute("visibility","hidden")})}
function bars(id){const host=document.getElementById(id),ds=D.days;if(!ds.length){host.innerHTML='<p class="muted">Bars appear after the first market close.</p>';return}
 const W=600,H=260,L=64,R=10,T=10,B=26;const[s,tip]=frame(host,W,H);
 const vals=ds.map(d=>d[2]);let lo=Math.min(0,...vals),hi=Math.max(0,...vals);const pad=(hi-lo)*.1||100;lo-=lo<0?pad:0;hi+=hi>0?pad:0;
 const y=v=>T+(hi-v)*(H-T-B)/(hi-lo),bw=(W-L-R)/ds.length;
 for(const v of ticks(lo,hi,4)){el("line",{x1:L,x2:W-R,y1:y(v),y2:y(v),stroke:css("--line")},s);const t=el("text",{x:L-8,y:y(v)+4,"text-anchor":"end","font-size":11,fill:css("--muted")},s);t.textContent=fmt(v)}
 ds.forEach((d,i)=>{const v=d[2],x0=L+i*bw+Math.max(2,bw*.18),w=Math.max(4,bw*.64),y0=y(Math.max(v,0)),h=Math.max(1,Math.abs(y(v)-y(0)));
  el("rect",{x:x0,y:y0,width:w,height:h,rx:4,fill:v>=0?css("--up"):css("--down")},s);
  const t=el("text",{x:x0+w/2,y:H-6,"text-anchor":"middle","font-size":11,fill:css("--muted")},s);t.textContent=d[0];
  const hit=el("rect",{x:L+i*bw,y:T,width:bw,height:H-T-B,fill:"transparent"},s);
  hit.addEventListener("pointermove",()=>{const r=s.getBoundingClientRect();tip.style.display="block";tip.style.left=((x0+w/2)*r.width/W)+"px";tip.style.top=(y0*r.height/H)+"px";tip.innerHTML=`${d[0]} · ${d[1]}<br><b class="${v>=0?"up":"down"}">${v>=0?"+":""}${fmt(v)}</b>`});
  hit.addEventListener("pointerleave",()=>tip.style.display="none")});
 el("line",{x1:L,x2:W-R,y1:y(0),y2:y(0),stroke:css("--axis"),"stroke-width":1},s)}
line("eq");bars("dp");
</script></body></html>
"""
