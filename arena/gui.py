"""Control panel in the browser: python -m arena gui

A small local web server (127.0.0.1 only) that serves the app in arena/web/
and a JSON API for it: start/stop Claude agents, spawn a fleet, live US and
Indian market quotes and charts, the trade journal and settings. Nothing is
exposed to the internet and, as everywhere in this app, no real orders exist.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import marketdata
from .config import ROOT, load_config
from .fleet import LOGS, PRESETS, Fleet
from .markets import MARKETS

log = logging.getLogger("arena")
WEB = Path(__file__).resolve().parent / "web"


class Controller:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path) if config_path else ROOT / "config.toml"
        self.fleet = Fleet(self.config_path)
        self.claude = {"checked": False}
        self.claude_lock = threading.Lock()
        self.side_task = ""  # demo / review running in the background
        threading.Thread(target=self._claude_info, daemon=True).start()

    # ---- Claude connection ------------------------------------------
    def _claude_info(self, ping: bool = False) -> dict:
        from .brain import claude_status
        with self.claude_lock:
            st = claude_status(run_ping=ping, model=load_config(self.config_path)["strategy"].get("cli_model", ""))
            if not ping and "ok" in self.claude:
                st.update({k: self.claude[k] for k in ("ok", "reply", "error", "seconds", "at") if k in self.claude})
            if ping:
                from datetime import datetime
                st["at"] = datetime.now().strftime("%H:%M:%S")
                log.info("Claude connection test: %s (%.1fs)", "connected" if st.get("ok") else st.get("error", "failed"),
                         st.get("seconds", 0))
            st["checked"] = True
            self.claude = st
            return st

    def check_claude(self) -> dict:
        return self._claude_info(ping=True)

    # ---- trading -----------------------------------------------------
    def start(self, region: str | None) -> dict:
        region = (region or load_config(self.config_path)["market"]["region"]).upper()
        rid = self.fleet.primary(region)
        msg = self.fleet.start(rid)
        return {"msg": msg, "id": rid}

    def stop(self, rid: int | None) -> dict:
        return {"msg": self.fleet.stop(rid) if rid else self.fleet.stop_all()}

    def demo(self, days: int = 3) -> str:
        if self.side_task:
            return f"Busy: {self.side_task}"

        def go():
            from .feeds.sim import SimFeed
            from .db import Journal
            from .runner import Runner
            from .markets import market_of
            try:
                self.side_task = "demo"
                cfg = self.fleet.base_cfg()
                cfg["strategy"]["brain"] = "rules"
                cfg["paths"]["db"] = str(Path(cfg["paths"]["data_dir"]) / "demo.db")
                cfg["account"]["challenge_days"] = days
                feed = SimFeed(cfg["market"]["watchlist"], n_days=days, market=market_of(cfg))
                j = Journal(cfg["paths"]["db"])
                rid = j.new_run("Demo on synthetic prices (rules)", "demo", "synthetic", "rules", "",
                                cfg["account"]["starting_capital"], cfg, feed.now().isoformat())
                log.info("DEMO: %d days of made-up prices. Not real market data.", days)
                Runner(cfg, feed, j, rid, report_path=ROOT / "reports" / "demo.html").run()
            except Exception:
                log.exception("Demo failed")
            finally:
                self.side_task = ""

        threading.Thread(target=go, daemon=True, name="demo").start()
        return "ok"

    def review(self, rid: int) -> str:
        if self.side_task:
            return f"Busy: {self.side_task}"

        def go():
            from .brain import make_brain
            from .db import Journal
            from .insights import ai_review
            from .report import write
            try:
                self.side_task = "coach review"
                j = Journal(self.fleet.db_path)
                run = j.one("SELECT * FROM runs WHERE id=?", (rid,))
                cfg = json.loads(run["config_json"])
                if cfg["strategy"]["brain"] == "rules":
                    cfg = self.fleet.base_cfg()
                if cfg["strategy"]["brain"] == "rules":
                    log.info("The coach review needs Claude (Claude Code login or API key).")
                    return
                log.info("Asking Claude for a coach review of '%s'...", run["name"])
                ai_review(make_brain(cfg), j, rid)
                write(j, rid, self.report_path(rid), live=False)
                log.info("Coach review added to the report.")
            except Exception:
                log.exception("Coach review failed")
            finally:
                self.side_task = ""

        threading.Thread(target=go, daemon=True, name="review").start()
        return "ok"

    def report_path(self, rid: int | None) -> Path:
        cfg = load_config(self.config_path)
        if rid:
            p = ROOT / "reports" / f"agent_{rid}.html"
            if p.exists():
                return p
        return Path(cfg["paths"]["report"])

    # ---- state -------------------------------------------------------
    def state(self) -> dict:
        import tomllib
        cfg = load_config(self.config_path)
        raw = tomllib.loads(self.config_path.read_text(encoding="utf-8-sig"))
        markets = {}
        for code, mk in MARKETS.items():
            hol = set(cfg["market"].get(f"holidays_{code.lower()}", []))
            now = mk.now()
            markets[code] = {
                "code": code, "name": mk.name, "exchange": mk.exchange, "currency": mk.currency, "symbol": mk.symbol,
                "locale": mk.locale, "tz": mk.tz_abbr, "open": mk.is_open(now, hol), "time": now.strftime("%H:%M:%S"),
                "next_open": mk.next_open(now, hol).strftime("%a %d %b, %H:%M"),
                "next_open_iso": mk.next_open(now, hol).isoformat(),
                "session": f"{mk.open:%H:%M}–{mk.close:%H:%M}",
                "watchlist": cfg["market"].get(f"watchlist_{code.lower()}") or list(mk.watchlist),
            }
        st = cfg["strategy"]
        return {
            "markets": markets,
            "region": cfg["market"]["region"],
            "claude": self.claude,
            "brain_setting": st["brain"],
            "side_task": self.side_task,
            "agents": self.fleet.summaries(),
            "running": self.fleet.running,
            "presets": PRESETS,
            "log": [json.loads(x) for x in list(LOGS.all)[-150:]],
            "settings": {
                "region": cfg["market"]["region"],
                "starting_capital": cfg["account"]["starting_capital"],
                "challenge_days": cfg["account"]["challenge_days"],
                "feed": cfg["market"].get("feed_in", "yahoo"),
                "watchlist_us": ", ".join(cfg["market"].get("watchlist_us") or MARKETS["US"].watchlist),
                "watchlist_in": ", ".join(cfg["market"].get("watchlist_in") or MARKETS["IN"].watchlist),
                "brain": st["brain"], "cli_model": st.get("cli_model", ""), "model": st.get("model", ""),
                "profile": st.get("profile", "balanced"),
                "decision_minutes": raw["strategy"]["decision_minutes"],
                "product": raw["strategy"]["product"],
                "max_positions": raw["strategy"]["max_positions"],
                "max_position_pct": raw["strategy"]["max_position_pct"],
                "max_daily_loss_pct": raw["strategy"]["max_daily_loss_pct"],
                "notes": st.get("notes", ""),
            },
        }

    def save_settings(self, s: dict) -> str:
        if self.fleet.running:
            return "Stop all agents before changing settings."
        text = self.config_path.read_text(encoding="utf-8-sig")

        def wl(v):
            return [w.strip().upper() for w in str(v or "").split(",") if w.strip()]

        updates = {
            ("market", "region"): "IN" if s.get("region") == "IN" else "US",
            ("account", "starting_capital"): int(float(s["starting_capital"])),
            ("account", "challenge_days"): max(1, int(s["challenge_days"])),
            ("market", "feed"): s["feed"] if s.get("feed") in ("yahoo", "upstox") else "yahoo",
            ("market", "watchlist_us"): wl(s.get("watchlist_us")) or list(MARKETS["US"].watchlist),
            ("market", "watchlist"): wl(s.get("watchlist_in")) or list(MARKETS["IN"].watchlist),
            ("strategy", "brain"): s["brain"] if s.get("brain") in ("auto", "claude-cli", "claude-api", "rules") else "auto",
            ("strategy", "cli_model"): str(s.get("cli_model", "")).strip(),
            ("strategy", "profile"): "aggressive" if s.get("profile") == "aggressive" else "balanced",
            ("strategy", "decision_minutes"): max(1, int(s["decision_minutes"])),
            ("strategy", "product"): "delivery" if s.get("product") == "delivery" else "intraday",
            ("strategy", "max_positions"): max(1, int(s["max_positions"])),
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


def _markers(detail: dict, symbol: str, region: str) -> list[dict]:
    out = []
    for f in detail.get("fills", []):
        if f["symbol"] != symbol:
            continue
        try:
            t = marketdata.local_epoch(f["ts"], region)
        except Exception:
            continue
        out.append({"time": t, "side": f["side"], "price": f["fill_price"], "qty": f["qty"], "trigger": f["trigger"]})
    return sorted(out, key=lambda m: m["time"])


def make_handler(ctl: Controller):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application")) else ""))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, default=str))

        def do_GET(self):
            u = urlparse(self.path)
            path, qs = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, (WEB / "index.html").read_bytes(), "text/html")
                if path.startswith("/static/"):
                    f = (WEB / path[len("/static/"):]).resolve()
                    if WEB.resolve() in f.parents and f.is_file():
                        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                        return self._send(200, f.read_bytes(), ctype)
                    return self._send(404, "{}")
                if path == "/api/state":
                    return self._json(ctl.state())
                if path == "/api/agent":
                    return self._json(ctl.fleet.detail(int(qs["id"])))
                if path == "/api/quotes":
                    region = qs.get("region", "US").upper()
                    syms = qs.get("symbols", "").split(",")
                    return self._json({"quotes": marketdata.quotes(region, syms), "indices": marketdata.indices(region)})
                if path == "/api/candles":
                    region = qs.get("region", "US").upper()
                    sym = qs["symbol"].upper()
                    data = marketdata.candles(region, sym, qs.get("interval", "5m"))
                    if qs.get("agent"):
                        data = dict(data, markers=_markers(ctl.fleet.detail(int(qs["agent"])), sym, region))
                    return self._json(data)
                if path in ("/report", "/demo"):
                    f = ctl.report_path(int(qs["id"]) if qs.get("id") else None) if path == "/report" else ROOT / "reports" / "demo.html"
                    if not f.exists():
                        return self._send(200, EMPTY, "text/html")
                    html = re.sub(r'<meta http-equiv="refresh"[^>]*>', "", f.read_text(encoding="utf-8"))
                    return self._send(200, html, "text/html")
            except Exception as e:
                log.warning("GET %s failed: %s", path, e)
                return self._json({"error": str(e)}, 500)
            self._send(404, "{}")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            path = urlparse(self.path).path
            try:
                if path == "/api/start":
                    return self._json(ctl.start(body.get("region")))
                if path == "/api/stop":
                    return self._json(ctl.stop(int(body["id"]) if body.get("id") else None))
                if path == "/api/agents":
                    rid = ctl.fleet.spawn(body, start=body.get("start", True))
                    return self._json({"msg": "ok", "id": rid})
                if path == "/api/agents/squad":
                    region = body.get("region") or "US"
                    keys = body.get("presets") or ["momentum", "reversion", "guardian"]
                    ids = [ctl.fleet.spawn({"preset": k, "region": region, "model": body.get("model"),
                                            "mode": body.get("mode"), "replay_days": body.get("replay_days")})
                           for k in keys]
                    return self._json({"msg": "ok", "ids": ids})
                if path == "/api/agents/start":
                    return self._json({"msg": ctl.fleet.start(int(body["id"]))})
                if path == "/api/agents/stop":
                    return self._json({"msg": ctl.fleet.stop(int(body["id"]))})
                if path == "/api/agents/remove":
                    return self._json({"msg": ctl.fleet.remove(int(body["id"]))})
                if path == "/api/claude/check":
                    return self._json(ctl.check_claude())
                if path == "/api/claude/refresh":
                    return self._json(ctl._claude_info())
                if path in ("/api/claude/login", "/api/claude/install"):
                    from .brain import claude_install, claude_login
                    return self._json({"msg": (claude_login if path.endswith("login") else claude_install)()})
                if path == "/api/demo":
                    return self._json({"msg": ctl.demo(int(body.get("days", 3)))})
                if path == "/api/review":
                    return self._json({"msg": ctl.review(int(body["id"]))})
                if path == "/api/settings":
                    return self._json({"msg": ctl.save_settings(body)})
            except Exception as e:
                log.warning("POST %s failed: %s", path, e)
                return self._json({"msg": f"Error: {e}"})
            self._send(404, "{}")

    return H


def serve(port: int = 8787, open_browser: bool = True) -> None:
    LOGS.setLevel(logging.INFO)
    logging.getLogger().addHandler(LOGS)
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    ctl = Controller()

    class Server(ThreadingHTTPServer):
        # On Windows SO_REUSEADDR lets a second process bind a port that is already in use,
        # so an old copy of the app would silently keep answering. Bind exclusively instead.
        allow_reuse_address = os.name != "nt"
        daemon_threads = True

    for p in range(port, port + 10):
        try:
            srv = Server(("127.0.0.1", p), make_handler(ctl))
            break
        except OSError:
            continue
    else:
        raise SystemExit("No free port between 8787 and 8796.")
    url = f"http://127.0.0.1:{p}/"
    print(f"\n  Breaking Money · AI Trading Arena is running at {url}\n  Keep this window open. Close it to quit.\n")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        ctl.fleet.stop_all()


EMPTY = """<!doctype html><html><head><meta charset="utf-8"><style>
:root{color-scheme:light dark}body{margin:0;height:100vh;display:grid;place-items:center;font:15px -apple-system,BlinkMacSystemFont,"Inter","Segoe UI",sans-serif;background:Canvas;color:GrayText}
</style></head><body><div style="text-align:center"><p>No report yet.<br>Start an agent to generate one.</p></div></body></html>"""
