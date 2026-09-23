@echo off
rem Launcher for PIA on Windows: the ONE-CLICK entry point.
rem   Double-click (no arguments): runs the full pipeline (fetch every source, triage, rank,
rem   write a briefing) and, ONLY if that succeeds, starts the web UI so the result opens in
rem   your browser. The web UI keeps running in this window until you close it; it fetches
rem   nothing itself and never calls Jev or Groq. If the pipeline fails, the web UI is not
rem   started, the failure is shown clearly, and the window stays open so you can read it.
rem   From a terminal, WITH arguments: `run-pia.bat <arguments>` runs only `pia <arguments>`
rem   (e.g. `run-pia.bat status`) - the pipeline/web-UI sequence above does not apply, there is
rem   no pause, and it is scriptable, exactly as before.
rem   Want the web UI on its own, without collecting anything new? Use run-pia-web.bat.
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
goto :pipeline

:passthrough
call "%PIA_EXE%" %*
exit /b %ERRORLEVEL%

:pipeline
echo ============================================================
echo  PIA - Personal Information Acquiring Agent
echo  [1/2] PIPELINE STARTING: fetch every source, triage, rank, write a briefing.
echo ============================================================
echo.

call "%PIA_EXE%"
set "PIA_EXIT=%ERRORLEVEL%"
echo.

if not "%PIA_EXIT%"=="0" goto :pipeline_failed

echo ============================================================
echo  [1/2] PIPELINE COMPLETE.
echo  [2/2] WEB UI STARTING - read-only, fetches nothing. Close this window to stop it.
echo ============================================================
echo.

call "%PIA_PYTHON%" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 goto :missing_extras

call "%PIA_EXE%" web --open
set "PIA_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %PIA_EXIT%

:pipeline_failed
echo ============================================================
echo  PIPELINE FAILED  (exit code %PIA_EXIT%^) - see the message above.
echo  The web UI was NOT started.
echo ============================================================
pause
exit /b %PIA_EXIT%

:missing_extras
echo The web UI needs extra packages. Install them once with:
echo     .venv\Scripts\python.exe -m pip install -e ".[web]"
pause
exit /b 1
