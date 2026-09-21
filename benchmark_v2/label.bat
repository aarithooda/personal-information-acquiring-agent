@echo off
rem Launcher for the PIA Benchmark v2 on Windows.
rem   Double-click:    starts (or resumes) labelling. Your progress is saved after every key press.
rem   From a terminal: benchmark_v2\label.bat status   (any `python -m benchmark_v2` arguments; no pause)
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo PIA is not installed yet. Follow the Setup steps in README.md.
    pause
    exit /b 1
)

if "%~1"=="" (
    ".venv\Scripts\python.exe" -m benchmark_v2 label
) else (
    ".venv\Scripts\python.exe" -m benchmark_v2 %*
)
set "B2_EXIT=%ERRORLEVEL%"

rem No arguments means it was most likely double-clicked: wait, so the window does not vanish.
if "%~1"=="" (
    echo.
    pause
)
exit /b %B2_EXIT%
