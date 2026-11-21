@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe call START-TRADING.bat --setup-only
.venv\Scripts\python -m arena report --ai || .venv\Scripts\python -m arena report
pause
