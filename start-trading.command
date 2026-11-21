#!/usr/bin/env bash
# Double-click on Mac (or run ./start-trading.command on Linux). Sets up on first run, then opens the Arena.
cd "$(dirname "$0")" || exit 1

find_py() {
  for c in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

PY=$(find_py)
if [ -z "$PY" ]; then
  echo
  echo "  Python 3.11 or newer is needed and was not found."
  if command -v brew >/dev/null 2>&1; then
    read -r -p "  Install Python 3.12 with Homebrew now? [Y/n] " a
    case "$a" in [nN]*) ;; *) brew install python@3.12 ;; esac
    PY=$(find_py)
  fi
fi
if [ -z "$PY" ]; then
  echo "  Install Python from https://www.python.org/downloads/ and run this again."
  { command -v open >/dev/null && open https://www.python.org/downloads/; } || true
  read -r -p "  Press Return to close." _
  exit 1
fi

"$PY" launch.py "$@" || { echo; echo "  Something went wrong. Read the message above."; read -r -p "  Press Return to close." _; }
