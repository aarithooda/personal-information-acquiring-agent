@echo off
rem Launcher for the PIA web UI (New, Library, Favorites) on Windows.
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

".venv\Scripts\pia.exe" web --open %*
set "PIA_EXIT=%ERRORLEVEL%"

rem No arguments means it was most likely double-clicked: wait, so any error message stays readable.
if "%~1"=="" (
    echo.
    pause
)
exit /b %PIA_EXIT%
