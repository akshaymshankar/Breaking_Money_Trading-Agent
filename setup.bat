@echo off
rem Installs everything without opening the app. START-TRADING.bat does this too.
cd /d "%~dp0"
call START-TRADING.bat --setup-only
