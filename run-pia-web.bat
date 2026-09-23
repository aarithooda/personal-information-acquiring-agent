@echo off
rem Launcher for the PIA web UI (New, Library, Favorites) on Windows: a MANUAL, view-only entry point.
rem run-pia.bat already starts this automatically after a successful pipeline run, so most people
rem never need this file directly. Use it when you want the web UI WITHOUT a fresh collection run:
rem re-opening it after closing the browser, browsing offline, or developing/debugging the web UI
rem on its own. READ-ONLY: it does NOT fetch anything and never talks to Jev or Groq.
rem   Double-click:    starts the local server and opens your browser. Close this window to stop it.
rem   From a terminal: run-pia-web.bat --port 9000   (any `pia web` options; no pause, so it is scriptable)
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pia.exe" (
    echo PIA is not installed yet. Follow the Setup steps in README.md.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo The web UI needs extra packages. Install them once with:
    echo     .venv\Scripts\python.exe -m pip install -e ".[web]"
    pause
    exit /b 1
)

if "%~1"=="" (
    echo ============================================================
    echo  PIA web UI - READ ONLY
    echo  Shows what run-pia.bat has already found. Fetches nothing itself.
    echo  Want a fresh check first? Close this and run run-pia.bat instead.
    echo ============================================================
    echo.
)

".venv\Scripts\pia.exe" web --open %*
set "PIA_EXIT=%ERRORLEVEL%"

rem No arguments means it was most likely double-clicked: wait, so any error message stays readable.
if "%~1"=="" (
    echo.
    pause
)
exit /b %PIA_EXIT%
