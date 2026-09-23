"""The double-click launcher exists because a bare `pia.exe` window closes the moment it finishes.

`run-pia.bat` with no arguments now runs the pipeline and, only on success, starts the web UI. That
real path needs real network, real API keys and a real briefing, none of which a test may use — so it
is exercised here against a STUB `pia.exe` (a small .bat) that the launcher runs instead, via the
`PIA_EXE`/`PIA_PYTHON` overrides it reads for exactly this purpose. The stub records what it was
called with, so the tests assert on what the launcher actually did (which command ran, in what order,
whether the web UI was started), not on the launcher's source text.
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


def write_stub_pia(tmp_path: Path, *, no_args_exit: int, calls_log: Path) -> Path:
    """A stub `pia.exe` (really a .bat): records every invocation to `calls_log`, one line per call.
    Exits `no_args_exit` when called with no arguments (simulating the pipeline); when called with
    `web --open` it records the call and exits 0 immediately, so the test never waits for a real server."""
    stub = tmp_path / "stub_pia.bat"
    stub.write_text(
        "@echo off\r\n"
        f'echo CALLED:%*>> "{calls_log}"\r\n'
        'if "%~1"=="" (\r\n'
        f"  exit /b {no_args_exit}\r\n"
        ")\r\n"
        "exit /b 0\r\n",
        encoding="ascii",
    )
    return stub


def write_stub_python(tmp_path: Path) -> Path:
    """A stub `python.exe` standing in for the `import fastapi, uvicorn` package check: always succeeds."""
    stub = tmp_path / "stub_python.bat"
    stub.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
    return stub


def test_passes_arguments_through_and_does_not_wait_for_a_keypress(tmp_path):
    result = run("--db", str(tmp_path / "pia.db"), "status")  # a hang here would hit the timeout
    assert result.returncode == 0
    assert "Last checked: never" in result.stdout


def test_propagates_the_exit_code_of_pia(tmp_path):
    result = run("--db", str(tmp_path / "pia.db"), "show")  # no briefings yet -> pia exits 1
    assert result.returncode == 1


def test_arguments_skip_the_pipeline_and_web_ui_sequence_entirely(tmp_path):
    """`run-pia.bat status` must run ONLY `pia status` - never the pipeline, never the web UI."""
    calls_log = tmp_path / "calls.log"
    stub = write_stub_pia(tmp_path, no_args_exit=0, calls_log=calls_log)
    result = run("status", env={"PIA_EXE": str(stub)})
    assert result.returncode == 0
    assert calls_log.read_text().strip() == "CALLED:status"


# ---------- A: the pipeline succeeds -> the web UI starts ----------


def test_a_successful_pipeline_starts_the_web_ui(tmp_path):
    calls_log = tmp_path / "calls.log"
    pia_stub = write_stub_pia(tmp_path, no_args_exit=0, calls_log=calls_log)
    python_stub = write_stub_python(tmp_path)

    result = run(env={"PIA_EXE": str(pia_stub), "PIA_PYTHON": str(python_stub)})

    assert result.returncode == 0
    calls = calls_log.read_text().splitlines()
    assert calls == ["CALLED:", "CALLED:web --open"], calls  # pipeline (no args), then the web UI, in order
    assert "PIPELINE STARTING" in result.stdout
    assert "PIPELINE COMPLETE" in result.stdout
    assert "WEB UI STARTING" in result.stdout
    assert "FAILED" not in result.stdout


# ---------- B: the pipeline fails -> the web UI does NOT start ----------


def test_a_failing_pipeline_does_not_start_the_web_ui(tmp_path):
    calls_log = tmp_path / "calls.log"
    pia_stub = write_stub_pia(tmp_path, no_args_exit=1, calls_log=calls_log)
    python_stub = write_stub_python(tmp_path)

    result = run(env={"PIA_EXE": str(pia_stub), "PIA_PYTHON": str(python_stub)})

    assert result.returncode == 1
    calls = calls_log.read_text().splitlines()
    assert calls == ["CALLED:"]  # only the pipeline attempt - the web UI was never invoked
    assert "PIPELINE STARTING" in result.stdout
    assert "PIPELINE FAILED" in result.stdout
    assert "exit code 1" in result.stdout
    assert "WEB UI STARTING" not in result.stdout


def test_a_missing_web_extra_is_reported_and_does_not_start_the_web_ui(tmp_path):
    """The package check runs after a successful pipeline; if it fails, the web UI still must not start."""
    calls_log = tmp_path / "calls.log"
    pia_stub = write_stub_pia(tmp_path, no_args_exit=0, calls_log=calls_log)
    failing_python = tmp_path / "failing_python.bat"
    failing_python.write_text("@echo off\r\nexit /b 1\r\n", encoding="ascii")

    result = run(env={"PIA_EXE": str(pia_stub), "PIA_PYTHON": str(failing_python)})

    assert result.returncode == 1
    assert calls_log.read_text().splitlines() == ["CALLED:"]  # pipeline ran; web UI did not
    assert 'pip install -e ".[web]"' in result.stdout


def test_only_pauses_after_the_pipeline_or_web_ui_step_not_for_scriptable_arguments():
    text = BAT.read_text()
    # `if not "%~1"=="" goto :passthrough` returns early (no pause) for the scriptable, argument-taking
    # path; every `pause` comes later, on the no-arguments (double-click) pipeline/web-UI path.
    early_return = text.index('if not "%~1"==""')
    first_pause = text.index("pause", early_return)
    assert early_return < first_pause
    assert text.count("pause") >= 2  # one for the failure path, one after the web UI stops


# ---------- web launcher ----------

WEB_BAT = Path(__file__).parent.parent / "run-pia-web.bat"


def run_web(*args: str, timeout=60):
    return subprocess.run(
        ["cmd", "/c", str(WEB_BAT), *args], capture_output=True, text=True, encoding="utf-8", timeout=timeout, stdin=subprocess.DEVNULL
    )


def test_the_web_launcher_passes_arguments_through_to_pia_web_without_starting_a_server():
    result = run_web("--help")  # `pia web --open --help` prints help and exits; a real start would hit the timeout
    assert result.returncode == 0
    assert "local web UI" in result.stdout and "--port" in result.stdout


def test_the_web_launcher_opens_the_browser_and_only_pauses_without_arguments():
    text = WEB_BAT.read_text()
    assert "web --open" in text
    assert 'if "%~1"==""' in text and text.index('if "%~1"==""') < text.index("pause", text.index('if "%~1"==""'))
