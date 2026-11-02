@echo off
cd /d %~dp0
set PY=python
where python >nul 2>nul || set PY=py -3
%PY% -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ is required'" || (
  echo.
  echo Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during install, then run this again.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe %PY% -m venv .venv
echo Installing packages (first time takes a few minutes)...
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -c "import pandas, numpy, requests, yfinance, anthropic, tzdata" || (
  echo.
  echo Package install FAILED. Check your internet connection and run setup.bat again.
  pause
  exit /b 1
)
if not exist .env copy .env.example .env >nul
echo.
echo Setup done. Double-click OPEN-ARENA.bat to open the Arena.
