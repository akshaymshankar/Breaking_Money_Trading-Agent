"""Desktop-style control panel in the browser: python -m arena gui

Runs a small local web server (127.0.0.1 only) with Start / Stop buttons,
settings, a live log and the results dashboard. Nothing is exposed to the
internet and, as everywhere in this app, no real orders exist.
"""
from __future__ import annotations

import collections
import json
import logging
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .clock import is_market_open, next_open, now_ist
from .config import ROOT, load_config
from .db import Journal

log = logging.getLogger("arena")
LOG_LINES: collections.deque[str] = collections.deque(maxlen=300)


class _Buffer(logging.Handler):
    def emit(self, record):
        LOG_LINES.append(self.format(record))


class Controller:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path) if config_path else ROOT / "config.toml"
        self.thread: threading.Thread | None = None
        self.runner = None
        self.mode = "idle"
        self.error = ""
        self.lock = threading.Lock()

    def cfg(self) -> dict:
        from .__main__ import _brain_cfg
        return _brain_cfg(load_config(self.config_path), None)

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _spawn(self, mode: str, target) -> str:
        with self.lock:
            if self.busy:
                return f"Already running ({self.mode}). Stop it first."
            self.mode, self.error = mode, ""

            def wrap():
                try:
                    target()
                except Exception as e:  # show it in the panel instead of dying silently
                    self.error = str(e)
                    log.exception("Stopped with an error")
                finally:
                    self.mode = "idle"
                    self.runner = None

            self.thread = threading.Thread(target=wrap, daemon=True)
            self.thread.start()
            return "ok"

    def start_live(self, new: bool, name: str) -> str:
        def go():
            from .feeds import make_live_feed
            from .runner import Runner
            cfg = self.cfg()
            j = Journal(cfg["paths"]["db"])
            feed = make_live_feed(cfg)
            run = None if new else j.active_run("live")
            if run:
                rid = run["id"]
                log.info("Resuming live run #%d '%s'", rid, run["name"])
            else:
                st = cfg["strategy"]
                rid = j.new_run(name or f"Live challenge {now_ist():%d %b %Y}", "live", feed.name, st["brain"],
                                st.get("model", ""), cfg["account"]["starting_capital"], cfg, now_ist().isoformat())
                log.info("Started live run #%d with Rs %s virtual capital", rid, f"{cfg['account']['starting_capital']:,}")
            log.info("Feed: %s | Brain: %s | PAPER ONLY", feed.name, cfg["strategy"]["brain"])
            self.runner = Runner(cfg, feed, j, rid)
            self.runner.write_report()
            self.runner.run()
        return self._spawn("live", go)

    def start_demo(self, days: int = 3) -> str:
        def go():
            from .feeds.sim import SimFeed
            from .runner import Runner
            cfg = self.cfg()
            cfg["strategy"]["brain"] = "rules"
            cfg["paths"]["db"] = str(Path(cfg["paths"]["data_dir"]) / "demo.db")
            cfg["account"]["challenge_days"] = days
            feed = SimFeed(cfg["market"]["watchlist"], n_days=days)
            j = Journal(cfg["paths"]["db"])
            rid = j.new_run("Demo on synthetic prices (rules)", "demo", "synthetic", "rules", "",
                            cfg["account"]["starting_capital"], cfg, feed.now().isoformat())
            log.info("DEMO: %d days of made-up prices. Not real market data.", days)
            self.runner = Runner(cfg, feed, j, rid, report_path=ROOT / "reports" / "demo.html")
            self.runner.run()
        return self._spawn("demo", go)

    def ai_review(self) -> str:
        def go():
            from .brain import make_brain
            from .insights import ai_review
            from .report import write
            cfg = self.cfg()
            j = Journal(cfg["paths"]["db"])
            run = j.one("SELECT * FROM runs WHERE mode='live' ORDER BY id DESC LIMIT 1")
            if not run:
                log.info("No live run yet. Start trading first.")
                return
            if cfg["strategy"]["brain"] == "rules":
                log.info("The coach review needs Claude (Claude Code login or API key).")
                return
            log.info("Asking Claude for today's coach review...")
            ai_review(make_brain(cfg), j, run["id"])
            write(j, run["id"], cfg["paths"]["report"], live=False)
            log.info("Coach review added to the dashboard.")
        return self._spawn("review", go)

    def live_run(self):
        cfg = load_config(self.config_path)
        j = Journal(cfg["paths"]["db"])
        run = j.one("SELECT id FROM runs WHERE mode='live' ORDER BY id DESC LIMIT 1")
        return j, (run["id"] if run else None)

    def stop(self) -> str:
        if self.runner:
            self.runner.stop_requested = True
            log.info("Stopping after the current step...")
            return "ok"
        return "Nothing is running."

    def state(self) -> dict:
        import tomllib
        cfg = load_config(self.config_path)
        raw = tomllib.loads(self.config_path.read_text(encoding="utf-8-sig"))  # settings form shows the file, not profile overrides
        resolved = self.cfg()["strategy"]["brain"]
        now = now_ist()
        holidays = set(cfg["market"].get("holidays", []))
        out = {
            "mode": self.mode, "busy": self.busy, "error": self.error,
            "market_open": is_market_open(now, holidays),
            "next_open": next_open(now, holidays).strftime("%a %d %b, %H:%M"),
            "now": now.strftime("%H:%M:%S"),
            "brain": resolved, "log": list(LOG_LINES)[-120:],
            "settings": {
                "starting_capital": cfg["account"]["starting_capital"],
                "challenge_days": cfg["account"]["challenge_days"],
                "feed": cfg["market"]["feed"],
                "watchlist": ", ".join(cfg["market"]["watchlist"]),
                "brain": cfg["strategy"]["brain"],
                "profile": cfg["strategy"].get("profile", "balanced"),
                "decision_minutes": raw["strategy"]["decision_minutes"],
                "product": raw["strategy"]["product"],
                "max_position_pct": raw["strategy"]["max_position_pct"],
                "max_daily_loss_pct": raw["strategy"]["max_daily_loss_pct"],
                "notes": cfg["strategy"].get("notes", ""),
            },
            "run": None,
        }
        j = Journal(cfg["paths"]["db"])
        run = j.one("SELECT * FROM runs WHERE mode='live' ORDER BY id DESC LIMIT 1")
        if run:
            acct = j.one("SELECT * FROM account WHERE run_id=?", (run["id"],))
            pos = j.q("SELECT symbol, qty, avg_price, last_price FROM positions WHERE run_id=?", (run["id"],))
            eq = acct["cash"] + sum(p["qty"] * (p["last_price"] or p["avg_price"]) for p in pos)
            days = j.one("SELECT COUNT(*) n FROM days WHERE run_id=?", (run["id"],))["n"]
            out["run"] = {"id": run["id"], "name": run["name"], "status": run["status"], "equity": round(eq, 2),
                          "pnl": round(eq - run["starting_capital"], 2), "positions": len(pos),
                          "days_done": days, "target": json.loads(run["config_json"])["account"]["challenge_days"]}
        return out

    def save_settings(self, s: dict) -> str:
        if self.busy:
            return "Stop trading before changing settings."
        text = self.config_path.read_text(encoding="utf-8-sig")
        wl = [w.strip().upper() for w in str(s.get("watchlist", "")).split(",") if w.strip()]
        updates = {
            ("account", "starting_capital"): int(float(s["starting_capital"])),
            ("account", "challenge_days"): int(s["challenge_days"]),
            ("market", "feed"): s["feed"] if s["feed"] in ("yahoo", "upstox") else "yahoo",
            ("market", "watchlist"): wl,
            ("strategy", "brain"): s["brain"] if s["brain"] in ("auto", "claude-cli", "claude-api", "rules") else "auto",
            ("strategy", "profile"): s.get("profile") if s.get("profile") in ("aggressive", "allin") else "balanced",
            ("strategy", "decision_minutes"): max(1, int(s["decision_minutes"])),
            ("strategy", "product"): "delivery" if s["product"] == "delivery" else "intraday",
            ("strategy", "max_position_pct"): float(s["max_position_pct"]),
            ("strategy", "max_daily_loss_pct"): float(s["max_daily_loss_pct"]),
            ("strategy", "notes"): str(s.get("notes", "")).replace("\n", " "),
        }
        for (section, key), val in updates.items():
            text = set_toml(text, section, key, val)
        self.config_path.write_text(text, encoding="utf-8")
        load_config(self.config_path)  # validate
        log.info("Settings saved.")
        return "ok"


def set_toml(text: str, section: str, key: str, value) -> str:
    lit = json.dumps(value, ensure_ascii=False)
    lines = text.split("\n")
    cur = None
    for i, line in enumerate(lines):
        m = re.match(r"\s*\[([^\]]+)\]", line)
        if m:
            cur = m.group(1).strip()
            continue
        if cur == section and re.match(rf"\s*{re.escape(key)}\s*=", line):
            comment = ""
            cm = re.search(r"(\s+#[^\"']*)$", line)
            if cm and not isinstance(value, str):
                comment = cm.group(1)
            lines[i] = f"{key} = {lit}{comment}"
            return "\n".join(lines)
    for i, line in enumerate(lines):  # key missing (older config): add it under its section
        if re.match(rf"\s*\[{re.escape(section)}\]", line):
            lines.insert(i + 1, f"{key} = {lit}")
            return "\n".join(lines)
    raise KeyError(f"[{section}] not found in config.toml")


def make_handler(ctl: Controller):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                return self._send(200, PANEL, "text/html")
            if path == "/api/state":
                return self._send(200, json.dumps(ctl.state()))
            if path in ("/report", "/demo"):
                cfg = load_config(ctl.config_path)
                f = Path(cfg["paths"]["report"]) if path == "/report" else ROOT / "reports" / "demo.html"
                if not f.exists():
                    return self._send(200, EMPTY, "text/html")
                html = re.sub(r'<meta http-equiv="refresh"[^>]*>', "", f.read_text(encoding="utf-8"))
                return self._send(200, html, "text/html")
            if path == "/api/days":
                j, rid = ctl.live_run()
                from .daily import run_days
                return self._send(200, json.dumps({"days": run_days(j, rid) if rid else []}))
            if path == "/download/day":
                from urllib.parse import parse_qs, urlparse
                from .daily import day_csv, day_html
                qs = parse_qs(urlparse(self.path).query)
                day, fmt = qs.get("date", [""])[0], qs.get("fmt", ["html"])[0]
                j, rid = ctl.live_run()
                if not rid or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                    return self._send(404, "{}")
                if fmt == "csv":
                    body, ctype, ext = day_csv(j, rid, day), "text/csv", "csv"
                else:
                    body, ctype, ext = day_html(j, rid, day), "text/html", "html"
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                if "view" not in qs:
                    self.send_header("Content-Disposition", f'attachment; filename="arena-pnl-{day}.{ext}"')
                self.end_headers()
                self.wfile.write(data)
                return
            self._send(404, "{}")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            path = self.path
            try:
                if path == "/api/start":
                    msg = ctl.start_live(bool(body.get("new")), body.get("name", ""))
                elif path == "/api/stop":
                    msg = ctl.stop()
                elif path == "/api/demo":
                    msg = ctl.start_demo(int(body.get("days", 3)))
                elif path == "/api/review":
                    msg = ctl.ai_review()
                elif path == "/api/settings":
                    msg = ctl.save_settings(body)
                else:
                    return self._send(404, "{}")
            except Exception as e:
                msg = f"Error: {e}"
            self._send(200, json.dumps({"msg": msg}))

    return H


def serve(port: int = 8787, open_browser: bool = True) -> None:
    h = _Buffer()
    h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(h)
    ctl = Controller()
    for p in range(port, port + 10):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), make_handler(ctl))
            break
        except OSError:
            continue
    else:
        raise SystemExit("No free port between 8787 and 8796.")
    url = f"http://127.0.0.1:{p}/"
    print(f"\n  AI Trading Arena is running at {url}\n  Keep this window open. Close it to quit.\n")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        ctl.stop()


EMPTY = """<!doctype html><html><body style="background:#0d0f14;color:#898781;font:15px system-ui;display:grid;place-items:center;height:90vh;margin:0">
<div style="text-align:center"><div style="font-size:42px">📈</div><p>No results yet.<br>Press <b style="color:#f0efec">Start trading</b> to begin, or <b style="color:#f0efec">Run demo</b> to preview.</p></div></body></html>"""

PANEL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Trading Arena</title>
<style>
:root{--page:#08090c;--surface:#12141a;--surface-2:#1a1d25;--line:#262a35;--ink:#f0efec;--ink-2:#c3c2b7;--muted:#898781;
--accent:#3987e5;--up:#0ca30c;--down:#d03b3b;--warn:#fab219}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;display:grid;grid-template-columns:340px 1fr;height:100vh}
aside{background:linear-gradient(180deg,#121826,var(--surface));border-right:1px solid var(--line);padding:18px 16px;overflow-y:auto;display:flex;flex-direction:column;gap:14px}
.brand{font-weight:800;letter-spacing:.14em;text-transform:uppercase;font-size:11px;color:var(--accent)}
h1{font-size:20px;margin:2px 0 0}
.badge{display:inline-block;border:1px solid rgba(250,178,25,.4);color:var(--warn);border-radius:999px;padding:3px 9px;font-size:10.5px;font-weight:700;letter-spacing:.08em;margin-top:8px}
.box{background:var(--page);border:1px solid var(--line);border-radius:12px;padding:12px}
.status{display:flex;align-items:center;gap:8px;font-weight:700}
.dot{width:10px;height:10px;border-radius:50%;background:var(--muted)}
.dot.on{background:var(--up);box-shadow:0 0 10px var(--up);animation:p 1.4s infinite}@keyframes p{50%{opacity:.3}}
.small{color:var(--muted);font-size:12px}
.big{font-size:26px;font-weight:800}.up{color:var(--up)}.down{color:var(--down)}
.row{display:flex;gap:8px;flex-wrap:wrap}
button{font:inherit;font-weight:700;border-radius:10px;border:1px solid var(--line);background:var(--surface-2);color:var(--ink);padding:10px 12px;cursor:pointer;flex:1}
button:hover{border-color:var(--accent)}button:disabled{opacity:.4;cursor:not-allowed}
button.go{background:var(--up);border-color:var(--up);color:#fff}button.stop{background:var(--down);border-color:var(--down);color:#fff}
label{display:block;font-size:11.5px;color:var(--muted);margin:8px 0 3px}
input,select,textarea{width:100%;font:inherit;background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 9px}
textarea{min-height:56px;resize:vertical}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 10px}
details summary{cursor:pointer;font-weight:700}
#log{font:11.5px/1.45 ui-monospace,Consolas,monospace;color:var(--ink-2);height:200px;overflow-y:auto;white-space:pre-wrap;word-break:break-word}
.dayrow{display:flex;gap:10px;align-items:center;padding:5px 0;border-bottom:1px solid var(--line)}.dayrow b{flex:1;color:var(--ink)}.dayrow a{color:var(--accent);text-decoration:none;font-weight:700}
#msg{min-height:18px;font-size:12px;color:var(--warn)}
main{display:flex;flex-direction:column;min-width:0}
.tabs{display:flex;gap:6px;padding:10px 14px;border-bottom:1px solid var(--line);background:var(--surface)}
.tab{flex:none;padding:7px 14px;font-size:13px}.tab.on{border-color:var(--accent);color:var(--accent)}
iframe{flex:1;border:0;width:100%;background:var(--page)}
@media (max-width:860px){body{grid-template-columns:1fr;height:auto}main{height:90vh}}
</style></head><body>
<aside>
  <div><div class="brand">Breaking Money</div><h1>AI Trading Arena</h1><span class="badge">PAPER ONLY · VIRTUAL ₹</span></div>
  <div class="box">
    <div class="status"><span class="dot" id="dot"></span><span id="status">Loading…</span></div>
    <div class="small" id="market"></div>
    <div class="small" id="brain"></div>
  </div>
  <div class="box" id="runbox" style="display:none">
    <div class="small" id="runname"></div>
    <div class="big" id="equity"></div>
    <div id="pnl" style="font-weight:700"></div>
    <div class="small" id="rundetail"></div>
  </div>
  <div class="row">
    <button class="go" id="bstart" onclick="start(false)">▶ Start trading</button>
    <button class="stop" id="bstop" onclick="post('/api/stop')">■ Stop</button>
  </div>
  <div class="row">
    <button id="bnew" onclick="startNew()">New challenge</button>
    <button id="breview" onclick="post('/api/review')">Coach review</button>
  </div>
  <button id="bdemo" onclick="post('/api/demo',{days:3});showTab('demo')">Run demo (made-up prices)</button>
  <div id="msg"></div>
  <div class="box"><div class="small" style="margin-bottom:6px">Daily P&amp;L reports</div><div id="days" class="small">No trading days yet.</div></div>
  <details class="box"><summary>Settings</summary>
    <div class="grid2">
      <div><label>Starting capital ₹</label><input id="s_starting_capital" type="number"></div>
      <div><label>Challenge days</label><input id="s_challenge_days" type="number"></div>
      <div><label>Who decides</label><select id="s_brain"><option value="auto">Auto</option><option value="claude-cli">Claude (my login)</option><option value="claude-api">Claude (API key)</option><option value="rules">Rules (free, no AI)</option></select></div>
      <div style="grid-column:1/-1"><label>Risk profile</label><select id="s_profile"><option value="balanced">Balanced (uses the numbers below)</option><option value="aggressive">Aggressive: 3 big bets, A+ setups only, 6% day limit</option><option value="allin">All-in: 1 trade, ~95% of cash (₹50k+), breakouts only</option></select></div>
      <div><label>Price feed</label><select id="s_feed"><option value="yahoo">Yahoo (free)</option><option value="upstox">Upstox</option></select></div>
      <div><label>Decide every (min)</label><input id="s_decision_minutes" type="number"></div>
      <div><label>Style</label><select id="s_product"><option value="intraday">Intraday</option><option value="delivery">Hold overnight</option></select></div>
      <div><label>Max per stock %</label><input id="s_max_position_pct" type="number"></div>
      <div><label>Daily loss stop %</label><input id="s_max_daily_loss_pct" type="number" step="0.5"></div>
    </div>
    <label>Watchlist (NSE symbols, comma separated)</label><textarea id="s_watchlist"></textarea>
    <label>Strategy notes for Claude</label><textarea id="s_notes" placeholder="e.g. Only trade breakouts with strong volume"></textarea>
    <div class="row" style="margin-top:10px"><button onclick="saveSettings()">Save settings</button></div>
  </details>
  <div class="box"><div class="small" style="margin-bottom:6px">Activity</div><div id="log"></div></div>
</aside>
<main>
  <div class="tabs"><button class="tab on" id="t_report" onclick="showTab('report')">Live dashboard</button><button class="tab" id="t_demo" onclick="showTab('demo')">Demo</button></div>
  <iframe id="frame" src="/report"></iframe>
</main>
<script>
let tab="report",loaded=false;
const $=id=>document.getElementById(id);
const rs=v=>(v<0?"−":"")+"₹"+Math.abs(v).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});
async function post(url,body){const r=await fetch(url,{method:"POST",body:JSON.stringify(body||{})});const j=await r.json();$("msg").textContent=j.msg==="ok"?"":j.msg;refresh()}
function start(n){post("/api/start",{new:n})}
function startNew(){const name=prompt("Name for the new challenge","Breaking Money Challenge");if(name!==null)post("/api/start",{new:true,name})}
function showTab(t){tab=t;$("t_report").classList.toggle("on",t==="report");$("t_demo").classList.toggle("on",t==="demo");reloadFrame()}
function reloadFrame(){const f=$("frame"),y=f.contentWindow?f.contentWindow.scrollY:0;f.onload=()=>{try{f.contentWindow.scrollTo(0,y)}catch(e){}};f.src="/"+tab+"?t="+Date.now()}
async function saveSettings(){const s={};["starting_capital","challenge_days","brain","profile","feed","decision_minutes","product","max_position_pct","max_daily_loss_pct","watchlist","notes"].forEach(k=>s[k]=$("s_"+k).value);post("/api/settings",s)}
async function refresh(){
 const s=await (await fetch("/api/state")).json();
 const labels={idle:"Idle",live:"Trading live",demo:"Running demo",review:"Writing coach review"};
 $("status").textContent=labels[s.mode]||s.mode;$("dot").classList.toggle("on",s.busy);
 $("market").textContent=(s.market_open?"Market OPEN · ":"Market closed · next open "+s.next_open+" · ")+"IST "+s.now;
 $("brain").textContent="Decisions by: "+({"claude-cli":"Claude (your login)","claude-api":"Claude (API key)","rules":"Rules strategy (no AI)"}[s.brain]||s.brain);
 $("bstart").disabled=s.busy;$("bnew").disabled=s.busy;$("bdemo").disabled=s.busy;$("breview").disabled=s.busy;$("bstop").disabled=!(s.busy&&s.mode!=="review");
 if(s.error)$("msg").textContent="Error: "+s.error;
 if(s.run){$("runbox").style.display="";$("runname").textContent=s.run.name+" · "+s.run.status;$("equity").textContent=rs(s.run.equity);
  $("pnl").textContent=(s.run.pnl>=0?"+":"")+rs(s.run.pnl)+" ("+(s.run.pnl/(s.run.equity-s.run.pnl)*100).toFixed(2)+"%)";$("pnl").className=s.run.pnl>0?"up":s.run.pnl<0?"down":"";
  $("rundetail").textContent="Day "+Math.min(s.run.days_done+(s.run.status==="active"?1:0),s.run.target)+" of "+s.run.target+" · "+s.run.positions+" open position(s)";
  $("bstart").textContent=s.run.status==="active"?"▶ Resume trading":"▶ Start trading"}
 const lg=$("log"),atBottom=lg.scrollTop+lg.clientHeight>=lg.scrollHeight-5;lg.textContent=s.log.join("\n");if(atBottom)lg.scrollTop=lg.scrollHeight;
 if(!loaded){for(const k in s.settings)if($("s_"+k))$("s_"+k).value=s.settings[k];loaded=true}
}
async function loadDays(){const d=(await (await fetch("/api/days")).json()).days;if(!d.length)return;
 $("days").innerHTML=d.map(x=>`<div class="dayrow"><b>${x}</b><a href="/download/day?date=${x}&view=1" target="_blank">View</a><a href="/download/day?date=${x}">Download</a><a href="/download/day?date=${x}&fmt=csv">CSV</a></div>`).join("")}
refresh();loadDays();setInterval(refresh,3000);setInterval(loadDays,60000);setInterval(reloadFrame,30000);
</script></body></html>"""
