@echo off
rem Launcher for PIA on Windows: the ONE-CLICK entry point.
rem   Double-click (no arguments): starts PIA - the web app plus the background agent that acquires,
rem   processes and ranks information - and opens it in your browser. It keeps running in this
rem   window; close the window (or press Ctrl+C) to stop it.
rem   From a terminal, WITH arguments: `run-pia.bat <arguments>` runs only `pia <arguments>`
rem   (e.g. `run-pia.bat run-once`), with no pause, so it is scriptable.
rem PIA_EXE / PIA_PYTHON exist so tests can substitute a stub; leave them unset to use the real
rem   venv (the default), which is what a normal install does.
setlocal
cd /d "%~dp0"
if not defined PIA_EXE set "PIA_EXE=.venv\Scripts\pia.exe"
if not defined PIA_PYTHON set "PIA_PYTHON=.venv\Scripts\python.exe"

if not exist "%PIA_EXE%" (
    echo PIA is not installed yet. Follow the Setup steps in README.md.
    pause
    exit /b 1
)

if not "%~1"=="" goto :passthrough
goto :serve

:passthrough
call "%PIA_EXE%" %*
exit /b %ERRORLEVEL%

:serve
call "%PIA_PYTHON%" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 goto :missing_extras

echo ============================================================
echo  PIA - Personal Information Acquiring Agent
echo  Starting the web app and the background agent.
echo  Close this window to stop PIA.
echo ============================================================
echo.

call "%PIA_EXE%" serve --open
set "PIA_EXIT=%ERRORLEVEL%"
echo.
if not "%PIA_EXIT%"=="0" echo PIA stopped with an error (exit code %PIA_EXIT%^) - see the message above.
pause
exit /b %PIA_EXIT%

:missing_extras
echo PIA's web app needs extra packages. Install them once with:
echo     .venv\Scripts\python.exe -m pip install -e ".[web]"
pause
exit /b 1
