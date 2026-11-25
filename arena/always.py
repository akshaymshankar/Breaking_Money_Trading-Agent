"""Keep the app running: one copy only, restart after a crash, start when you sign in to Windows.

- `OneCopy`: two copies on one journal overwrite each other's trades, so the second one refuses to open.
- `keep_running`: opens the app and opens it again if it crashes. Closing its window ends it.
- `autostart_*`: a shortcut in your own Startup folder (Windows only). No admin rights, no service.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import ROOT, load_config

log = logging.getLogger("arena")

ALREADY_OPEN = 3          # exit code under keep_running: another copy has the journal
DEFAULT_PORT = 8787
SHORTCUT = "Breaking Money Arena.lnk"


class OneCopy:
    """A lock the operating system drops by itself when the program ends, however it ends."""

    def __init__(self, folder: str | Path, name: str = "arena"):
        self.folder = Path(folder)
        self.lock_path = self.folder / f"{name}.lock"
        self.port_path = self.folder / f"{name}.port"
        self.fh = None

    def take(self) -> bool:
        self.folder.mkdir(parents=True, exist_ok=True)
        fh = open(self.lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self.fh = fh
        return True

    def drop(self) -> None:
        if not self.fh:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.fh.close()
        self.fh = None

    def clear_port(self) -> None:
        self.port_path.unlink(missing_ok=True)

    def set_port(self, port: int) -> None:
        self.port_path.write_text(json.dumps({"port": port}), encoding="utf-8")

    def port(self) -> int | None:
        try:
            return int(json.loads(self.port_path.read_text(encoding="utf-8"))["port"])
        except Exception:
            return None


def journal_folder(cfg: dict) -> Path:
    return Path(cfg["paths"]["db"]).parent


# ---- keep running ------------------------------------------------------
def _plugged_in() -> bool:
    class Power(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("pct", ctypes.c_ubyte), ("r", ctypes.c_ubyte),
                    ("life", ctypes.c_ulong), ("full", ctypes.c_ulong)]
    p = Power()
    return bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(p))) and p.ac == 1


def stay_awake(on: bool) -> None:
    """Ask Windows not to sleep by itself while plugged in. Changes no power setting and ends with the program."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if on and _plugged_in() else 0x80000000)
    except Exception:
        pass


def _wants_awake() -> bool:
    try:
        return load_config().get("autopilot", {}).get("enabled", False) is True
    except Exception:
        return False


def next_wait(fails: int) -> int:
    return min(300, 10 * 2 ** min(fails, 6))


def keep_running(port: int = DEFAULT_PORT, start=None, sleep=time.sleep, clock=time.time) -> int:
    """Open the app and keep it open. `start` returns a process-like object (for tests)."""
    one = OneCopy(journal_folder(load_config()), "always")
    if not one.take():
        print("Already kept running by another window.")
        return 0

    def open_app():
        return subprocess.Popen([sys.executable, "-m", "arena", "gui", "--no-open", "--port", str(port)], cwd=str(ROOT),
                                env={**os.environ, "ARENA_SUPERVISED": "1"})
    start, fails = start or open_app, 0
    print("\n  Breaking Money Arena is kept running from this window. Close it to stop the app.\n")
    try:
        while True:
            t0, p = clock(), start()
            while p.poll() is None:
                stay_awake(_wants_awake())
                sleep(20)
            rc = p.poll()
            if rc == 0:
                return 0                       # closed on purpose
            if rc == ALREADY_OPEN:
                sleep(60)                      # another copy is open: take over when it closes
                continue
            fails = 0 if clock() - t0 > 600 else fails + 1
            wait = next_wait(fails)
            print(f"  The app stopped (code {rc}). Opening it again in {wait} seconds.")
            sleep(wait)
    except KeyboardInterrupt:
        return 0
    finally:
        stay_awake(False)
        one.drop()


# ---- start with Windows --------------------------------------------------
def startup_folder() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def autostart_on(folder: Path | None = None) -> Path:
    if os.name != "nt":
        raise RuntimeError("Start with the computer is Windows only for now.")
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        raise RuntimeError("Run START-TRADING once first, so the app's own Python exists.")
    link = (folder or startup_folder()) / SHORTCUT
    link.parent.mkdir(parents=True, exist_ok=True)
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:ARENA_LINK);$s.TargetPath=$env:ARENA_PY;"
              "$s.Arguments='-m arena always';$s.WorkingDirectory=$env:ARENA_ROOT;$s.WindowStyle=7;"
              "$s.Description='Breaking Money Arena, paper trading';$s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=60,
                       env={**os.environ, "ARENA_LINK": str(link), "ARENA_PY": str(py), "ARENA_ROOT": str(ROOT)})
    if r.returncode != 0 or not link.exists():
        raise RuntimeError("Windows did not create the shortcut: " + (r.stderr or "").strip()[:200])
    return link


def autostart_off(folder: Path | None = None) -> None:
    (folder or startup_folder()).joinpath(SHORTCUT).unlink(missing_ok=True)


def autostart_state(folder: Path | None = None) -> bool:
    return os.name == "nt" and (folder or startup_folder()).joinpath(SHORTCUT).exists()
