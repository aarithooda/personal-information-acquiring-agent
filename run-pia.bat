@echo off
rem Launcher for PIA on Windows.
rem   Double-click:   runs `pia` (your briefing) and keeps the window open so you can read it.
rem   From a terminal: run-pia.bat status   (any pia arguments; no pause, so it is scriptable)
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pia.exe" (
    echo PIA is not installed yet. Follow the Setup steps in README.md.
    pause
    exit /b 1
)

".venv\Scripts\pia.exe" %*
set "PIA_EXIT=%ERRORLEVEL%"

rem No arguments means it was most likely double-clicked: wait, so the window does not vanish.
if "%~1"=="" (
    echo.
    pause
)
exit /b %PIA_EXIT%
