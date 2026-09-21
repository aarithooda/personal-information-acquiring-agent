"""The double-click launcher exists because a bare `pia.exe` window closes the moment it finishes.

Only the with-arguments path is executed here: the no-argument path runs the real pipeline
(network, your real database), which a test must never do.
"""

import subprocess
import sys
from pathlib import Path

import pytest

BAT = Path(__file__).parent.parent / "run-pia.bat"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher")


def run(*args: str, timeout=60):
    return subprocess.run(
        ["cmd", "/c", str(BAT), *args], capture_output=True, text=True, encoding="utf-8", timeout=timeout, stdin=subprocess.DEVNULL
    )


def test_passes_arguments_through_and_does_not_wait_for_a_keypress(tmp_path):
    result = run("--db", str(tmp_path / "pia.db"), "status")  # a hang here would hit the timeout
    assert result.returncode == 0
    assert "Last checked: never" in result.stdout


def test_propagates_the_exit_code_of_pia(tmp_path):
    result = run("--db", str(tmp_path / "pia.db"), "show")  # no briefings yet -> pia exits 1
    assert result.returncode == 1


def test_only_pauses_when_there_are_no_arguments():
    text = BAT.read_text()
    assert 'if "%~1"==""' in text and text.index('if "%~1"==""') < text.index("pause", text.index('if "%~1"==""'))
