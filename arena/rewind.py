"""Rewind: scrub through one stock's trading day bar by bar, showing only what was knowable at each moment
(price, indicators, Claude's calls and the open position), with the rest of the day hidden until you move forward.
Works on the live run's day or on a time-travel replay. Read-only: it never trades and never writes to a journal."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .clock import now_ist
from .db import Journal
from .feeds.base import Quote
from .indicators import snapshot

_cache: dict[tuple[str, str, str], dict] = {}


def _bars(symbol: str) -> pd.DataFrame:
    from .feeds.yahoo import YahooFeed

    df = YahooFeed()._download([symbol], period="60d", interval="5m").get(symbol)
    if df is None or df.empty:
        raise ValueError(f"No 5-minute prices for {symbol}")
    return df


def trading_days(symbol: str) -> list[str]:
    return sorted({d.isoformat() for d in _bars(symbol).index.date}, reverse=True)


def day_data(cfg: dict, symbol: str, day: str, source: str = "live") -> dict:
    symbol = symbol.upper()
    key = (symbol, day, source)
    if key in _cache:
        return _cache[key]
    d = date.fromisoformat(day)
    bars = _bars(symbol)
    bars = bars[bars.index.date <= d]
    today = bars[bars.index.date == d]
    if today.empty:
        raise ValueError(f"No prices for {symbol} on {day} (Yahoo keeps about 60 days of 5-minute data)")
    prev = bars[bars.index.date < d]
    prev_close = float(prev["close"].iloc[-1]) if len(prev) else float(today["open"].iloc[0])

    frames = []
    for ts, r in today.iterrows():
        upto = today.loc[:ts]
        q = Quote(symbol=symbol, ltp=float(r["close"]), open=float(upto["open"].iloc[0]), high=float(upto["high"].max()),
                  low=float(upto["low"].min()), prev_close=prev_close, volume=float(upto["volume"].sum()),
                  ts=ts.to_pydatetime(), bar_volume=float(r["volume"]))
        frames.append({"t": ts.strftime("%H:%M"), "o": round(float(r["open"]), 2), "h": round(float(r["high"]), 2),
                       "l": round(float(r["low"]), 2), "c": round(float(r["close"]), 2), "v": int(r["volume"]),
                       "snap": snapshot(q, bars.loc[:ts])})

    if source == "replay":  # the latest time-travel replay of this stock and day
        db = Path(cfg["paths"]["data_dir"]) / "timetravel.db"
        j = Journal(db) if db.exists() else None
        run = j.one("SELECT id, name FROM runs WHERE mode='timetravel' AND name=? ORDER BY id DESC LIMIT 1",
                    (f"Time travel · {symbol} · {day}",)) if j else None
    else:
        j = Journal(cfg["paths"]["db"])
        run = j.one("SELECT r.id, r.name FROM runs r JOIN decisions x ON x.run_id=r.id WHERE r.mode='live' "
                    "AND substr(x.ts,1,10)=? ORDER BY r.id DESC LIMIT 1", (day,))
    decisions, views, trades = [], [], []
    if run:
        rid = run["id"]
        decisions = j.q("SELECT substr(ts,12,5) t, action, confidence, stop_loss_pct, target_pct, reason, outcome, brain "
                        "FROM decisions WHERE run_id=? AND symbol=? AND substr(ts,1,10)=? ORDER BY id", (rid, symbol, day))
        views = j.q("SELECT substr(ts,12,5) t, market_view FROM decisions WHERE run_id=? AND substr(ts,1,10)=? "
                    "GROUP BY ts ORDER BY ts", (rid, day))
        trades = j.q("SELECT qty, substr(entry_ts,12,5) entry_t, entry_price, substr(exit_ts,12,5) exit_t, exit_price, "
                     "gross_pnl, charges, net_pnl, exit_reason, entry_reason FROM trades "
                     "WHERE run_id=? AND symbol=? AND substr(entry_ts,1,10)=? ORDER BY id", (rid, symbol, day))
    out = {"symbol": symbol, "day": day, "prev_close": round(prev_close, 2), "bars": frames, "decisions": decisions,
           "views": [v for v in views if v["market_view"]], "trades": trades, "run": run["name"] if run else None,
           "source": source}
    if d < now_ist().date() and source == "live":  # finished days never change
        _cache[key] = out
    return out


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rewind</title>
<style>
:root{--page:#08090c;--surface:#12141a;--surface-2:#1a1d25;--line:#262a35;--ink:#f0efec;--ink-2:#c3c2b7;--muted:#898781;
--accent:#3987e5;--glow:rgba(57,135,229,.35);--up:#0ca30c;--down:#d03b3b;--warn:#fab219;--violet:#9085e9}
*{box-sizing:border-box}html,body{margin:0}
body{background:radial-gradient(1200px 600px at 20% -10%,#132036 0,transparent 60%),var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding:18px 16px 40px}
.wrap{max-width:1240px;margin:0 auto}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;justify-content:space-between;margin-bottom:14px}
.brand{font-weight:800;letter-spacing:.14em;text-transform:uppercase;font-size:12px;color:var(--accent)}
h1{margin:2px 0 0;font-size:22px}.sub{color:var(--muted);font-size:12px}
.ctl{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
select,input,button{font:inherit;background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 10px}
button{font-weight:700;cursor:pointer;background:var(--surface-2)}button:hover{border-color:var(--accent)}button.on{border-color:var(--accent);color:var(--accent)}
.grid{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:14px}
.card{background:linear-gradient(180deg,var(--surface-2),var(--surface));border:1px solid var(--line);border-radius:14px;padding:16px;min-width:0}
h2{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2);margin:0 0 10px;display:flex;justify-content:space-between;gap:8px}
h2 small{color:var(--muted);font-weight:400;letter-spacing:0;text-transform:none}
.clock{font-size:40px;font-weight:800;line-height:1;text-shadow:0 0 24px var(--glow);font-variant-numeric:tabular-nums}
.px{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}.muted{color:var(--muted)}
svg{display:block;width:100%;height:auto}
.bar{display:flex;gap:8px;align-items:center;margin-top:10px}
input[type=range]{flex:1;padding:0;accent-color:var(--accent)}
.kv{display:grid;grid-template-columns:1fr auto;gap:5px 10px;font-size:13px}.kv span{color:var(--muted)}.kv b{font-variant-numeric:tabular-nums;text-align:right}
.call{border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--page);margin-top:8px}
.act{font-size:10.5px;font-weight:800;padding:2px 7px;border-radius:6px;letter-spacing:.06em}
.act.BUY{background:rgba(12,163,12,.16);color:var(--up)}.act.SELL{background:rgba(250,178,25,.14);color:var(--warn)}.act.SHORT{background:rgba(208,59,59,.16);color:var(--down)}.act.HOLD{background:#ffffff10;color:var(--muted)}
.view{border-left:3px solid var(--violet);padding:6px 10px;color:var(--ink-2);background:var(--page);border-radius:0 8px 8px 0;font-size:13px}
.pos{border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--page)}
.future{border:1px dashed #3a3f4d;border-radius:10px;padding:10px;font-size:13px;color:var(--ink-2)}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);margin-top:6px}.legend i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:5px;border-radius:2px}
.tl{display:flex;flex-direction:column;gap:6px;max-height:280px;overflow-y:auto}
.tl div{font-size:12.5px;color:var(--ink-2);cursor:pointer;padding:4px 6px;border-radius:6px}.tl div:hover{background:#ffffff08}.tl div.past{opacity:1}.tl div.fut{opacity:.35}
#err{color:var(--warn);min-height:18px}
@media (max-width:900px){.grid{grid-template-columns:1fr}.clock{font-size:32px}}
</style></head><body><div class="wrap">
<header><div><div class="brand">Breaking Money · AI Trading Arena</div><h1>⏱ Rewind <span id="title" class="muted" style="font-weight:600"></span></h1>
<div class="sub">Step through a day bar by bar. You only see what the Arena could see at that moment; the rest of the day stays hidden until you move forward.</div></div>
<div class="ctl"><select id="sym"></select><select id="day"></select><select id="src"><option value="live">Live run's calls</option><option value="replay">Time-travel replay's calls</option></select><button onclick="load()">Rewind</button></div></header>
<div id="err"></div>
<div class="grid">
 <div class="card">
  <h2><span>Price as of <span id="hdrt">—</span></span><small id="note"></small></h2>
  <div id="chart"></div>
  <div class="legend"><span><i style="background:#c3c2b7"></i>5-min candles</span><span><i style="background:var(--violet)"></i>VWAP</span><span><i style="background:var(--warn)"></i>SMA9</span><span><i style="background:var(--accent)"></i>SMA21</span><span><i style="background:#ffffff22;height:8px"></i>Opening range 09:15–09:45</span><span>▲ buy · ▼ short · ◆ sell/cover · · hold</span></div>
  <div class="bar"><button id="bplay" onclick="play()">▶ Play</button><button onclick="step(-1)">◀</button><input id="slider" type="range" min="0" value="0" oninput="go(+this.value)"><button onclick="step(1)">▶</button>
   <select id="speed"><option value="600">1x</option><option value="250" selected>2x</option><option value="90">5x</option></select><button id="bfut" onclick="toggleFuture()">Reveal future</button></div>
 </div>
 <div class="card">
  <div class="muted" style="font-size:12px">IST</div><div class="clock" id="clock">--:--</div>
  <div style="margin:8px 0 12px"><span class="px" id="px">—</span> <b id="chg"></b></div>
  <h2>What the Arena saw</h2><div class="kv" id="kv"></div>
  <h2 style="margin-top:14px">Position</h2><div class="pos" id="pos">Flat.</div>
 </div>
 <div class="card"><h2>Claude's calls on this stock <small>as of the cursor</small></h2><div id="calls" class="muted">No calls yet.</div>
  <h2 style="margin-top:14px">Claude's market view</h2><div class="view" id="view">—</div></div>
 <div class="card"><h2>Timeline <small>click to jump</small></h2><div class="tl" id="tl"></div>
  <h2 style="margin-top:14px">Hindsight from here</h2><div class="future" id="fut">Move the cursor to see how the rest of the day played out.</div></div>
</div></div>
<script>
const $=id=>document.getElementById(id);
const rs=v=>(v<0?"−":"")+"₹"+Math.abs(v).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});
const f2=v=>v==null?"—":(+v).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});
let D=null,i=0,timer=null,showFuture=false;
const qs=new URLSearchParams(location.search);
async function init(){
 const m=await (await fetch("/api/rewind/meta")).json();
 $("sym").innerHTML=m.watchlist.map(s=>`<option>${s}</option>`).join("");$("sym").value=qs.get("symbol")||"LT";$("src").value=qs.get("source")||"live";
 await days(qs.get("date"));$("sym").onchange=()=>days();load();
}
async function days(want){const r=await (await fetch("/api/rewind/days?symbol="+$("sym").value)).json();
 $("day").innerHTML=(r.days||[]).map(d=>`<option>${d}</option>`).join("");
 const today=new Date(Date.now()+5.5*3600e3).toISOString().slice(0,10);
 $("day").value=want&&r.days.includes(want)?want:(r.days.find(d=>d<today)||r.days[0]);}
async function load(){
 stop();$("err").textContent="Loading prices…";
 const r=await fetch(`/api/rewind?symbol=${$("sym").value}&date=${$("day").value}&source=${$("src").value}`);const j=await r.json();
 if(j.error){$("err").textContent=j.error;return}
 D=j;$("err").textContent="";$("title").textContent="· "+D.symbol+" · "+D.day;
 $("note").textContent=D.run?("Calls from: "+D.run):(D.source==="replay"?"No time-travel replay of this day yet: run one from the Time travel box":"No Arena calls recorded on this day");
 $("slider").max=D.bars.length-1;
 const first=D.decisions.find(x=>x.action!=="HOLD");go(first?Math.max(0,idx(first.t)-6):0);
 history.replaceState(null,"",`?symbol=${D.symbol}&date=${D.day}&source=${D.source}`);
}
const idx=t=>{let k=0;D.bars.forEach((b,n)=>{if(b.t<=t)k=n});return k};
function step(d){go(Math.min(D.bars.length-1,Math.max(0,i+d)))}
function play(){if(timer){stop();return}if(i>=D.bars.length-1)go(0);$("bplay").textContent="❚❚ Pause";
 timer=setInterval(()=>{if(i>=D.bars.length-1){stop();return}step(1)},+$("speed").value)}
function stop(){clearInterval(timer);timer=null;$("bplay").textContent="▶ Play"}
function toggleFuture(){showFuture=!showFuture;$("bfut").classList.toggle("on",showFuture);go(i)}
document.addEventListener("keydown",e=>{if(!D)return;if(e.key==="ArrowRight")step(1);if(e.key==="ArrowLeft")step(-1);if(e.key===" "){e.preventDefault();play()}});
function go(n){i=n;$("slider").value=n;const b=D.bars[n],s=b.snap,t=b.t;
 $("clock").textContent=t;$("hdrt").textContent=t;$("px").textContent="₹"+f2(b.c);
 const ch=(b.c/D.prev_close-1)*100;$("chg").textContent=(ch>=0?"+":"")+ch.toFixed(2)+"%";$("chg").className=ch>=0?"up":"down";
 const row=(k,v,c)=>`<span>${k}</span><b class="${c||""}">${v}</b>`;
 $("kv").innerHTML=row("Day open / high / low",`${f2(s.day_open)} / ${f2(s.day_high)} / ${f2(s.day_low)}`)
  +row("VWAP",s.vwap?f2(s.vwap)+(s.above_vwap?" · above":" · below"):"—",s.vwap?(s.above_vwap?"up":"down"):"")
  +row("SMA9 / SMA21",s.sma9?`${f2(s.sma9)} / ${f2(s.sma21)}`:"warming up",s.sma9?(s.sma9>s.sma21?"up":"down"):"")
  +row("RSI 14",s.rsi14??"—")+row("Momentum 30 min",s.mom_30m_pct!=null?(s.mom_30m_pct>=0?"+":"")+s.mom_30m_pct+"%":"—",s.mom_30m_pct>=0?"up":"down")
  +row("Volume vs avg",s.vol_ratio!=null?s.vol_ratio+"x":"—",s.vol_ratio>=1.2?"up":"")
  +row("Opening range",s.or_high?`${f2(s.or_low)} – ${f2(s.or_high)}`:"forming (till 09:45)")
  +row("ATR",s.atr_pct!=null?s.atr_pct+"%":"—");
 const past=D.decisions.filter(x=>x.t<=t),acts=past.filter(x=>x.action!=="HOLD"),last=past[past.length-1];
 $("calls").innerHTML=past.length?[...acts.slice(-3)].reverse().concat(last&&last.action==="HOLD"?[last]:[]).map(x=>`<div class="call"><span class="act ${x.action}">${x.action}</span> <b>${x.t}</b> <span class="muted">conf ${(+x.confidence).toFixed(2)}</span><div style="margin-top:4px;font-size:13px;color:var(--ink-2)">${esc(x.reason)}</div>${x.outcome?`<div class="muted" style="font-size:12px;margin-top:3px">${esc(x.outcome)}</div>`:""}</div>`).join(""):`<span class="muted">No calls on ${D.symbol} yet at ${t}.</span>`;
 const v=D.views.filter(x=>x.t<=t).pop();$("view").innerHTML=v?`<b>${v.t}</b> ${esc(v.market_view)}`:"—";
 // position at the cursor
 const open=D.trades.find(x=>x.entry_t<=t&&(!x.exit_t||x.exit_t>t)),done=D.trades.filter(x=>x.exit_t&&x.exit_t<=t);
 let ph="";if(open){const side=open.qty<0?"Short":"Long",q=Math.abs(open.qty),u=(b.c-open.entry_price)*open.qty;
  ph+=`<b>${side} ${q} @ ₹${f2(open.entry_price)}</b> <span class="muted">since ${open.entry_t} · size ${rs(q*open.entry_price)}</span><div class="px ${u>=0?"up":"down"}" style="margin-top:4px">${u>=0?"+":""}${rs(u)}</div><div class="muted" style="font-size:12px">unrealised, before charges</div>`}
 done.forEach(x=>{ph+=`<div style="margin-top:6px;font-size:13px">Closed ${x.qty<0?"short":"long"} ${Math.abs(x.qty)}: ${x.entry_t}→${x.exit_t}, net <b class="${x.net_pnl>=0?"up":"down"}">${x.net_pnl>=0?"+":""}${rs(x.net_pnl)}</b></div>`});
 $("pos").innerHTML=ph||"Flat.";
 // hindsight
 const rest=D.bars.slice(n+1);if(rest.length){const hi=Math.max(...rest.map(x=>x.h)),lo=Math.min(...rest.map(x=>x.l)),cl=D.bars[D.bars.length-1].c,pc=x=>((x/b.c-1)*100).toFixed(2);
  $("fut").innerHTML=`From ₹${f2(b.c)} at ${t}, the rest of the day went:<br>high <b class="up">₹${f2(hi)}</b> (${pc(hi)}%) · low <b class="down">₹${f2(lo)}</b> (${pc(lo)}%) · close <b>₹${f2(cl)}</b> (${pc(cl)}%)`}else $("fut").textContent="End of day.";
 tl(t);draw();
}
function tl(t){const ev=[];D.decisions.filter(x=>x.action!=="HOLD").forEach(x=>ev.push([x.t,`<span class="act ${x.action}">${x.action}</span> Claude: ${esc((x.outcome||x.reason).slice(0,90))}`]));
 D.trades.forEach(x=>{if(x.exit_t)ev.push([x.exit_t,`Closed (${x.exit_reason}) net <b class="${x.net_pnl>=0?"up":"down"}">${rs(x.net_pnl)}</b>`])});
 ev.push(["09:45","Opening range set"]);ev.sort((a,b)=>a[0]<b[0]?-1:1);
 $("tl").innerHTML=ev.map(e=>`<div class="${e[0]<=t?"past":"fut"}" onclick="go(idx('${e[0]}'))"><b>${e[0]}</b> ${e[1]}</div>`).join("")}
function esc(s){return String(s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}
function draw(){
 const W=900,H=380,P={l:58,r:14,t:14,b:26},VH=60,bars=D.bars,N=bars.length,vis=showFuture?bars:bars.slice(0,i+1);
 const lo=Math.min(...bars.map(b=>b.l)),hi=Math.max(...bars.map(b=>b.h)),pad=(hi-lo)*.06,y0=lo-pad,y1=hi+pad;
 const cw=(W-P.l-P.r)/N,X=k=>P.l+cw*(k+.5),Y=v=>P.t+(y1-v)/(y1-y0)*(H-P.t-P.b-VH);
 const vmax=Math.max(...bars.map(b=>b.v)),VY=v=>H-P.b-v/vmax*(VH-8);
 let g="";for(let k=0;k<=4;k++){const v=y0+(y1-y0)*k/4;g+=`<line x1="${P.l}" x2="${W-P.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#262a35"/><text x="${P.l-6}" y="${Y(v)+4}" fill="#898781" font-size="11" text-anchor="end">${v.toFixed(0)}</text>`}
 bars.forEach((b,k)=>{if(k%12===0)g+=`<text x="${X(k)}" y="${H-8}" fill="#898781" font-size="11" text-anchor="middle">${b.t}</text>`});
 const s=bars[Math.min(i,N-1)].snap,orI=bars.findIndex(b=>b.t>="09:45");
 if(s.or_high&&orI>0)g+=`<rect x="${P.l}" width="${W-P.l-P.r}" y="${Y(s.or_high)}" height="${Y(s.or_low)-Y(s.or_high)}" fill="#ffffff0d"/>`;
 // trade spans
 D.trades.forEach(x=>{const a=idx(x.entry_t),z=x.exit_t?idx(x.exit_t):N-1;if(a>i&&!showFuture)return;const zz=showFuture?z:Math.min(z,i);
  g+=`<rect x="${X(a)-cw/2}" width="${(zz-a+1)*cw}" y="${P.t}" height="${H-P.t-P.b-VH}" fill="${x.net_pnl>=0?"#0ca30c":"#d03b3b"}" opacity=".08"/>`;
  g+=`<line x1="${X(a)}" x2="${X(zz)}" y1="${Y(x.entry_price)}" y2="${Y(x.entry_price)}" stroke="#c3c2b7" stroke-dasharray="4 3" opacity=".7"/>`});
 const line=(key,col)=>{const p=vis.map((b,k)=>b.snap[key]!=null?`${X(k)},${Y(b.snap[key])}`:null).filter(Boolean).join(" ");return p?`<polyline points="${p}" fill="none" stroke="${col}" stroke-width="1.6"/>`:""};
 vis.forEach((b,k)=>{const up=b.c>=b.o,col=k>i?"#3a3f4d":(up?"#0ca30c":"#d03b3b");
  g+=`<line x1="${X(k)}" x2="${X(k)}" y1="${Y(b.h)}" y2="${Y(b.l)}" stroke="${col}"/><rect x="${X(k)-cw*.35}" width="${cw*.7}" y="${Y(Math.max(b.o,b.c))}" height="${Math.max(1,Math.abs(Y(b.o)-Y(b.c)))}" fill="${col}"/>`;
  g+=`<rect x="${X(k)-cw*.35}" width="${cw*.7}" y="${VY(b.v)}" height="${H-P.b-VY(b.v)}" fill="${col}" opacity=".45"/>`});
 g+=line("vwap","#9085e9")+line("sma9","#fab219")+line("sma21","#3987e5");
 D.decisions.forEach(x=>{const k=idx(x.t);if(k>i&&!showFuture)return;const b=bars[k];
  if(x.action==="BUY")g+=`<path d="M${X(k)} ${Y(b.l)+6} l6 11 h-12z" fill="#0ca30c"/>`;
  else if(x.action==="SHORT")g+=`<path d="M${X(k)} ${Y(b.h)-6} l6 -11 h-12z" fill="#d03b3b"/>`;
  else if(x.action==="SELL")g+=`<path d="M${X(k)} ${Y(b.h)-14} l6 6 l-6 6 l-6 -6z" fill="#fab219"/>`;
  else g+=`<circle cx="${X(k)}" cy="${P.t+4}" r="2" fill="#898781"/>`});
 g+=`<line x1="${X(i)}" x2="${X(i)}" y1="${P.t}" y2="${H-P.b}" stroke="#3987e5" stroke-width="1.5"/><circle cx="${X(i)}" cy="${Y(bars[i].c)}" r="4.5" fill="#3987e5" stroke="#08090c" stroke-width="2"/>`;
 if(!showFuture&&i<N-1)g+=`<rect x="${X(i)+cw/2}" width="${W-P.r-X(i)-cw/2}" y="${P.t}" height="${H-P.t-P.b}" fill="url(#fog)"/>`;
 $("chart").innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${D.symbol} 5-minute chart up to ${bars[i].t}"><defs><linearGradient id="fog" x1="0" x2="1"><stop offset="0" stop-color="#3987e5" stop-opacity=".10"/><stop offset="1" stop-color="#3987e5" stop-opacity=".02"/></linearGradient></defs>${g}</svg>`;
}
init();
</script></body></html>"""
