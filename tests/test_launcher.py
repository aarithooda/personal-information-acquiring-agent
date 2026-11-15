"""The double-click launcher exists because a bare `pia.exe` window closes the moment it finishes.

`run-pia.bat` with no arguments starts the app (`pia serve --open`); with arguments it runs only that command.
The real no-argument path starts a server, so it is exercised here against a STUB `pia.exe` (a small .bat) that
the launcher runs instead, via the `PIA_EXE`/`PIA_PYTHON` overrides it reads for exactly this purpose. The stub
records what it was called with, so the tests assert on what the launcher actually did, not on its source text.
"""

import subprocess
import sys
from pathlib import Path

import pytest

BAT = Path(__file__).parent.parent / "run-pia.bat"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher")


def run(*args: str, timeout=60, env=None):
    import os

    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        ["cmd", "/c", str(BAT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        env=full_env,
    )


def write_stub_pia(tmp_path: Path, *, serve_exit: int, calls_log: Path) -> Path:
    """A stub `pia.exe` (really a .bat): records every invocation to `calls_log`, one line per call, and exits
    `serve_exit` when called with `serve --open` (so the test never waits for a real server); 0 otherwise."""
    stub = tmp_path / "stub_pia.bat"
    stub.write_text(
        "@echo off\r\n"
        f'echo CALLED:%*>> "{calls_log}"\r\n'
        'if "%~1"=="serve" (\r\n'
        f"  exit /b {serve_exit}\r\n"
        ")\r\n"
        "exit /b 0\r\n",
        encoding="ascii",
    )
    return stub


def write_stub_python(tmp_path: Path, exit_code: int = 0) -> Path:
    """A stub `python.exe` standing in for the `import fastapi, uvicorn` package check."""
    stub = tmp_path / f"stub_python_{exit_code}.bat"
    stub.write_text(f"@echo off\r\nexit /b {exit_code}\r\n", encoding="ascii")
    return stub


def test_passes_arguments_through_and_does_not_wait_for_a_keypress(tmp_path):
    result = run("--db", str(tmp_path / "pia.db"), "status")  # a hang here would hit the timeout
    assert result.returncode == 0
    assert "Last checked: never" in result.stdout


def test_propagates_the_exit_code_of_pia(tmp_path):
    result = run("--db", str(tmp_path / "pia.db"), "show")  # no briefings yet -> pia exits 1
    assert result.returncode == 1


def test_arguments_run_only_that_command_and_never_start_the_app(tmp_path):
    calls_log = tmp_path / "calls.log"
    stub = write_stub_pia(tmp_path, serve_exit=0, calls_log=calls_log)
    result = run("run-once", "--no-fetch", env={"PIA_EXE": str(stub)})
    assert result.returncode == 0
    assert calls_log.read_text().strip() == "CALLED:run-once --no-fetch"


def test_a_double_click_starts_the_app_with_its_agent_and_opens_the_browser(tmp_path):
    calls_log = tmp_path / "calls.log"
    result = run(env={"PIA_EXE": str(write_stub_pia(tmp_path, serve_exit=0, calls_log=calls_log)), "PIA_PYTHON": str(write_stub_python(tmp_path))})
    assert result.returncode == 0
    assert calls_log.read_text().splitlines() == ["CALLED:serve --open"]
    assert "background agent" in result.stdout and "error" not in result.stdout.lower()


def test_a_failed_start_is_reported_with_its_exit_code(tmp_path):
    calls_log = tmp_path / "calls.log"
    result = run(env={"PIA_EXE": str(write_stub_pia(tmp_path, serve_exit=3, calls_log=calls_log)), "PIA_PYTHON": str(write_stub_python(tmp_path))})
    assert result.returncode == 3
    assert "exit code 3" in result.stdout


def test_missing_web_packages_are_explained_and_nothing_is_started(tmp_path):
    calls_log = tmp_path / "calls.log"
    result = run(env={"PIA_EXE": str(write_stub_pia(tmp_path, serve_exit=0, calls_log=calls_log)), "PIA_PYTHON": str(write_stub_python(tmp_path, 1))})
    assert result.returncode == 1
    assert not calls_log.exists()
    assert 'pip install -e ".[web]"' in result.stdout


def test_only_the_double_click_path_pauses():
    text = BAT.read_text()
    early_return = text.index('if not "%~1"==""')
    assert early_return < text.index("pause", early_return)  # the scriptable path returns before any pause
    assert "pause" not in text[text.index(":passthrough") : text.index(":serve")]
