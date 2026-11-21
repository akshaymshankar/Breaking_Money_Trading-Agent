@echo off
setlocal EnableDelayedExpansion
title Breaking Money - AI Trading Arena
cd /d "%~dp0"

rem Find Python 3.11 or newer. Tries PATH, the py launcher, then the usual install folders.
set "PY="
for %%C in ("python" "py -3.13" "py -3.12" "py -3.11" "py -3") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=%%~C"
  )
)
if not defined PY call :find_installed

if not defined PY (
  echo.
  echo   Python 3.11 or newer is needed and was not found on this computer.
  where winget >nul 2>nul
  if errorlevel 1 goto :no_python
  choice /C YN /M "  Install Python 3.12 now from the official Windows Package Manager"
  if errorlevel 2 goto :no_python
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
  call :find_installed
  if not defined PY goto :no_python
)

%PY% launch.py %*
if errorlevel 1 (
  echo.
  echo   Something went wrong. Read the message above, then close this window.
  pause
)
exit /b

:find_installed
for %%V in (313 312 311) do (
  if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe""
  if not defined PY if exist "%ProgramFiles%\Python%%V\python.exe" set "PY="%ProgramFiles%\Python%%V\python.exe""
)
exit /b

:no_python
echo.
echo   Install Python from https://www.python.org/downloads/
echo   On the first installer screen, tick "Add python.exe to PATH".
echo   Then double-click START-TRADING.bat again.
start "" https://www.python.org/downloads/
pause
exit /b 1
