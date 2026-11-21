"""One-click launcher: sets everything up on first run, then opens the Arena.

Run by START-TRADING.bat (Windows) and start-trading.command (Mac / Linux).
Uses only the Python standard library, so it works before anything is installed.

Every run it:
  1. creates the private Python environment in .venv (first run only),
  2. installs or repairs the packages from requirements.txt when they are
     missing, broken, or the file changed,
  3. makes sure Claude Code is installed and signed in (asks first),
  4. opens the control panel in your browser.

Flags: --setup-only (stop after step 3), --skip-claude (skip step 3),
--yes (answer yes to every question). Any other flags go to `arena gui`
(for example --port 8790 or --no-open).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
WIN = os.name == "nt"
VPY = VENV / ("Scripts/python.exe" if WIN else "bin/python")
STAMP = VENV / ".arena-installed"
REQ = ROOT / "requirements.txt"
INSTALL_CMD = (
    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "irm https://claude.ai/install.ps1 | iex"]
    if WIN else ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"]
)


def say(msg: str = "") -> None:
    print("  " + msg if msg else "", flush=True)


def step(n: int, msg: str) -> None:
    print(f"\n  [{n}/4] {msg}", flush=True)


def ask(question: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        return input(f"  {question} [Y/n] ").strip().lower() in ("", "y", "yes")
    except EOFError:
        return False


# ---- 1 + 2: Python environment ---------------------------------------
def req_hash() -> str:
    return hashlib.sha256(REQ.read_bytes()).hexdigest()[:16]


def env_ok() -> bool:
    if not VPY.exists() or not STAMP.exists() or STAMP.read_text().strip() != req_hash():
        return False
    return _probe_ok()


def setup_env() -> None:
    step(1, "Python environment")
    if not VPY.exists():
        say("Creating a private environment in .venv (first run only)...")
        if VENV.exists():
            shutil.rmtree(VENV, ignore_errors=True)
        venv.EnvBuilder(with_pip=True).create(VENV)
    say("Ready.")

    step(2, "App packages")
    if env_ok():
        say("Already installed.")
        return
    say("Installing pandas, yfinance and friends. Takes 1 to 3 minutes the first time...")
    pip = [str(VPY), "-m", "pip", "--disable-pip-version-check"]
    subprocess.run(pip + ["install", "--upgrade", "pip", "-q"])
    r = subprocess.run(pip + ["install", "-r", str(REQ), "-q"])
    if r.returncode != 0 or not _probe_ok():
        say("A package came out broken. Reinstalling it cleanly...")
        r = subprocess.run(pip + ["install", "--force-reinstall", "--no-cache-dir", "-r", str(REQ), "-q"])
    if r.returncode != 0 or not _probe_ok():
        say("")
        say("Could not install the packages. Check your internet connection and run this again.")
        say("If it keeps failing, delete the .venv folder and try once more.")
        sys.exit(1)
    STAMP.write_text(req_hash())
    say("Installed.")


def _probe_ok() -> bool:
    probe = "import pandas, numpy, requests, yfinance, anthropic, tzdata; pandas.util"
    return subprocess.run([str(VPY), "-c", probe], capture_output=True).returncode == 0


# ---- 3: Claude Code ---------------------------------------------------
def find_claude() -> str | None:
    exe = shutil.which("claude")
    if exe:
        return exe
    home = Path.home()
    for c in (home / ".local/bin/claude.exe", home / ".local/bin/claude", home / "AppData/Roaming/npm/claude.cmd",
              Path("/usr/local/bin/claude"), Path("/opt/homebrew/bin/claude")):
        if c.exists():
            return str(c)
    return None


def logged_in(exe: str) -> bool | None:
    try:
        r = subprocess.run([exe, "auth", "status", "--json"], capture_output=True, text=True, timeout=30)
        return bool(json.loads(r.stdout or "{}").get("loggedIn"))
    except Exception:
        return None


def setup_claude(auto_yes: bool) -> None:
    step(3, "Claude Code (lets Claude trade using your Claude Pro / Max login)")
    env_file = ROOT / ".env"
    if not env_file.exists() and (ROOT / ".env.example").exists():
        shutil.copy(ROOT / ".env.example", env_file)
    exe = find_claude()
    if not exe:
        say("Claude Code is not installed.")
        if ask("Install it now? (official installer from claude.ai)", auto_yes):
            subprocess.run(INSTALL_CMD)
            exe = find_claude()
        if not exe:
            say("Skipped. You can install it later from the app (sidebar button) or https://claude.com/claude-code")
            say("Until then the app trades with its free built-in rules.")
            return
    state = logged_in(exe)
    if state is False:
        say("Claude Code is installed but not signed in.")
        if ask("Sign in now? A browser window opens for your Claude account.", auto_yes):
            subprocess.run([exe, "auth", "login"])
            state = logged_in(exe)
        if state is False:
            say("Not signed in yet. Use 'Sign in with Claude' in the app's sidebar whenever you're ready.")
            return
    say("Connected." if state else "Installed.")


# ---- 4: launch --------------------------------------------------------
def main() -> None:
    args = sys.argv[1:]
    setup_only = "--setup-only" in args
    skip_claude = "--skip-claude" in args
    auto_yes = "--yes" in args
    passthru = [a for a in args if a not in ("--setup-only", "--skip-claude", "--yes")]

    if sys.version_info < (3, 11):
        say(f"Python 3.11 or newer is needed (this is {sys.version.split()[0]}).")
        say("Install it from https://www.python.org/downloads/ and run this again.")
        sys.exit(1)

    print("\n  Breaking Money · AI Trading Arena · paper trading, virtual money only")
    os.chdir(ROOT)
    setup_env()
    if skip_claude:
        step(3, "Claude Code check skipped")
    else:
        setup_claude(auto_yes)
    if setup_only:
        print("\n  Setup done. Double-click START-TRADING to open the Arena.\n")
        return
    step(4, "Opening the control panel in your browser...")
    sys.exit(subprocess.call([str(VPY), "-m", "arena", "gui", *passthru]))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
