@echo off
cd /d %~dp0
where claude >nul 2>nul || for /f "delims=" %%p in ('powershell -NoProfile -Command "(Get-ChildItem $env:APPDATA\Claude\claude-code -Recurse -Filter claude.exe -EA 0 | Sort-Object LastWriteTime -Descending | Select-Object -First 1).DirectoryName"') do set "PATH=%%p;%PATH%"
.venv\Scripts\python -c "import pandas, yfinance, tzdata" >nul 2>nul || call setup.bat || exit /b 1
.venv\Scripts\python -m arena gui
pause
