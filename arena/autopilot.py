"""Auto-run: starts paper sessions by itself, so nobody has to press Start.

US: the main agent is started for every session and started again if it stops on an error. Other US agents that were
running when the app closed are started again too, so their stops keep being watched.
India: one practice replay each trading day. India has no live sessions (compliance.py), so each replay uses the newest
session, more than 30 days old, that has not been replayed yet.

Pressing Stop wins: a stopped market stays stopped until its next session. Auto-run only presses the same buttons a
person would, so the spending lock, the risk rules and the India delay all still apply.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import compliance
from .config import load_config
from .markets import IN, MARKETS, US, Market

log = logging.getLogger("arena")

EARLY = timedelta(minutes=10)     # arm the US agent this long before the open
RETRY_SECONDS = 300               # after an error, wait this long before starting again
PRACTICE_FROM = time(9, 20)       # India practice starts after this, India time
PRACTICE_TRIES = 3                # per day, when the prices cannot be loaded
LOOK_BACK = 25                    # days of older sessions to choose from


def session_of(mk: Market, now: datetime, holidays: set[str]) -> date:
    """The session that is running, or the next one."""
    now = now.astimezone(mk.tz)
    if mk.is_trading_day(now.date(), holidays) and now.time() < mk.close:
        return now.date()
    return mk.next_open(now, holidays).date()


def practice_session(now: datetime, holidays: set[str], done=()) -> date | None:
    """The newest Indian session whose prices may be used and that has not been practised."""
    newest = compliance.last_day(IN, now)
    for i in range(LOOK_BACK):
        d = newest - timedelta(days=i)
        if IN.is_trading_day(d, holidays) and d.isoformat() not in done:
            return d
    return None


class Autopilot:
    def __init__(self, fleet, state_file: Path | str | None = None):
        self.fleet = fleet
        self.state_file = Path(state_file) if state_file else Path(fleet.db_path).parent / "autopilot.json"
        self.lock = threading.RLock()     # held while deciding and starting, and while a person stops something
        self.tried: dict[str, float] = {}
        self.note = ""
        self.resumed = False
        self.data, self.damaged = self._read()

    # ---- settings and saved state ------------------------------------
    def cfg(self) -> dict:
        return load_config(self.fleet.config_path)

    def enabled(self, cfg: dict | None = None) -> bool:
        return (self.cfg() if cfg is None else cfg).get("autopilot", {}).get("enabled", False) is True

    def _read(self) -> tuple[dict, bool]:
        try:
            d = json.loads(self.state_file.read_text(encoding="utf-8"))
            return (d, False) if isinstance(d, dict) else ({}, True)
        except FileNotFoundError:
            return {}, False
        except Exception:
            return {}, True               # it may have held a Stop: see tick()

    def _save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except OSError as e:
            log.warning("Could not save auto-run state: %s", str(e)[:120])

    def _holidays(self, cfg: dict | None, mk: Market) -> set[str]:
        try:
            return set((cfg or self.cfg())["market"].get(f"holidays_{mk.code.lower()}", []))
        except Exception:
            return set()

    # ---- a person pressed Stop or Start ------------------------------
    def hold(self, region: str, now: datetime | None = None) -> None:
        """Stop pressed: leave this market alone until its next session. Works even if config.toml is broken."""
        mk = US if region == "US" else IN
        with self.lock:
            self.data.setdefault("hold", {})[mk.code] = session_of(mk, now or mk.now(), self._holidays(None, mk)).isoformat()
            self._save()

    def release(self, region: str) -> None:
        with self.lock:
            if self.data.get("hold", {}).pop("US" if region == "US" else "IN", None):
                self._save()

    def forget(self, rid: int) -> None:
        """A person stopped this agent: do not bring it back after a restart."""
        with self.lock:
            live = self.data.get("live", [])
            if rid in live:
                live.remove(rid)
                self._save()

    def held(self, mk: Market, now: datetime, cfg: dict) -> bool:
        return self.data.get("hold", {}).get(mk.code) == session_of(mk, now, self._holidays(cfg, mk)).isoformat()

    # ---- the clock ---------------------------------------------------
    def tick(self, now: datetime | None = None) -> list[str]:
        """Called every half minute. Returns what it did."""
        with self.lock:
            cfg = self.cfg()
            if not self.enabled(cfg):
                self.note = ""
                return []
            now = now or US.now()
            if self.damaged:              # fail closed: a lost Stop must not become a Start
                self.damaged = False
                log.warning("Auto-run's saved state was damaged, so both markets wait for their next session.")
                for mk in (US, IN):
                    self.hold(mk.code, now)
            done, notes = [], []
            for fn in (self._us, self._india):
                try:
                    did, note = fn(now, cfg)
                    done += did
                    notes.append(note)
                except Exception as e:
                    log.exception("Auto-run problem")
                    notes.append(f"problem: {str(e)[:80]}")
            self.note = " · ".join(n for n in notes if n)
            return done

    def _ready(self, key: str) -> bool:
        if _time.time() - self.tried.get(key, 0) < RETRY_SECONDS:
            return False
        self.tried[key] = _time.time()
        return True

    def _us(self, now: datetime, cfg: dict) -> tuple[list[str], str]:
        hol = self._holidays(cfg, US)
        is_open = US.is_open(now, hol)
        nxt = US.next_open(now, hol)
        when = f"US next {nxt.astimezone(IN.tz):%a %H:%M} IST"
        if self.held(US, now, cfg):
            return [], "US stopped by you until the next session"
        did = self._resume_others() if is_open else []
        live, main = self._running("US")
        if live != self.data.get("live", []):
            self.data["live"] = live
            self._save()
        if main:
            return did, "US running" if is_open else when
        if not (is_open or nxt - now.astimezone(US.tz) <= EARLY):
            return did, when
        if not self._ready("US"):
            return did, "US starting again shortly"
        rid = self.fleet.primary("US")
        msg = self.fleet.start(rid)
        log.info("Auto-run: US session, agent #%d: %s", rid, msg)
        return did + [f"US:{rid}:{msg}"], "US starting"

    def _resume_others(self) -> list[str]:
        """Once after the app opens: agents that were running when it closed."""
        if self.resumed:
            return []
        self.resumed = True
        out, j = [], self.fleet.journal()
        try:
            for rid in list(self.data.get("live", [])):
                run = j.one("SELECT mode, status FROM runs WHERE id=?", (rid,))
                if run and run["mode"] == "live" and run["status"] == "active" and rid not in self.fleet.running:
                    out.append(f"US:{rid}:{self.fleet.start(rid)}")
        finally:
            j.conn.close()
        return out

    def _india(self, now: datetime, cfg: dict) -> tuple[list[str], str]:
        if cfg.get("autopilot", {}).get("india_practice", True) is not True:
            return [], ""
        hol = self._holidays(cfg, IN)
        local = now.astimezone(IN.tz)
        today = local.date().isoformat()
        if self.held(IN, now, cfg):
            return [], "India practice stopped by you for that day"
        if self._running("IN")[0]:
            return [], "India practising"
        if not IN.is_trading_day(local.date(), hol) or not PRACTICE_FROM <= local.time() < IN.close:
            return [], "India practice on trading days at 09:20 IST"
        seen, done_today = self._practised(today)
        if done_today:
            return [], "India practice done for today"
        tries = self.data.get("tries", {})
        if tries.get("day") == today and tries.get("n", 0) >= PRACTICE_TRIES:
            return [], "India practice could not load today"
        want = practice_session(now, hol, seen | set(self.data.get("asked", [])))
        if want is None:
            return [], "India: every older session is practised"
        if not self._ready("IN"):
            return [], ""
        self.data["tries"] = {"day": today, "n": (tries.get("n", 0) if tries.get("day") == today else 0) + 1}
        self._save()
        rid = self.fleet.spawn({"region": "IN", "mode": "replay", "replay_days": 1, "persona": "",
                                "replay_until": want.isoformat(), "name": "Auto practice IN"}, start=False)
        msg = self.fleet.start(rid)
        self.data["asked_now"] = want.isoformat()
        log.info("Auto-run: India practice on an older session, agent #%d: %s", rid, msg)
        return [f"IN:{rid}:{msg}"], "India practising"

    def _practised(self, today: str) -> tuple[set[str], bool]:
        """Sessions already replayed (by anyone), and whether one was replayed today."""
        j = self.fleet.journal()
        try:
            rows = j.q("SELECT r.id, r.started_at, substr(e.ts,1,10) d, json_extract(r.config_json,'$.market.region') region "
                       "FROM runs r JOIN equity e ON e.run_id=r.id WHERE r.mode LIKE 'replay%' GROUP BY r.id, d")
        finally:
            j.conn.close()
        mine = [r for r in rows if r["region"] == "IN"]
        done_today = any(str(r["started_at"])[:10] == today for r in mine)
        asked = self.data.pop("asked_now", None)
        if asked:                         # a holiday replays an older day instead: do not ask for it again
            if done_today:
                self.data["asked"] = (self.data.get("asked", []) + [asked])[-60:]
            self._save()
        return {r["d"] for r in mine}, done_today

    def _running(self, region: str) -> tuple[list[int], bool]:
        """Running agents in this market, and whether the main US agent is one of them."""
        out, main, j = [], False, self.fleet.journal()
        try:
            for rid in self.fleet.running:
                run = j.one("SELECT mode, config_json FROM runs WHERE id=?", (rid,))
                c = json.loads(run["config_json"]) if run else {}
                if not run or str(c.get("market", {}).get("region") or "IN").upper() != region:
                    continue
                if region == "US" and not run["mode"].startswith("live"):
                    continue
                out.append(rid)
                main = main or bool(c.get("agent", {}).get("primary") or "agent" not in c)
        finally:
            j.conn.close()
        return sorted(out), main

    def status(self) -> dict:
        try:
            on = self.enabled()
        except Exception:
            on = False
        return {"enabled": on, "note": (self.note or "starting") if on else ""}


def run_forever(pilot: Autopilot, every_five_minutes=None, seconds: float = 30) -> None:
    last = 0.0
    while True:
        try:
            pilot.tick()
            if every_five_minutes and _time.time() - last > 300:
                last = _time.time()
                every_five_minutes()
        except Exception:
            log.exception("Auto-run loop")
        _time.sleep(seconds)
