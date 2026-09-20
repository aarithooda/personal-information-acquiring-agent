from fakes import T0, FakeSource, make_item
from typer.testing import CliRunner

from pia.cli import app
from pia.db import connect
from pia.http import SourceError
from pia.pipeline import run_briefing

runner = CliRunner()


def test_output_survives_a_legacy_windows_encoding(monkeypatch):
    import io
    import sys

    from pia.cli import ensure_utf8_output

    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    ensure_utf8_output()
    print("Māori paper • bullet")  # would raise UnicodeEncodeError under cp1252
    sys.stdout.flush()
    assert "Māori".encode("utf-8") in raw.getvalue()


def test_status_on_a_fresh_database(tmp_path):
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "status"])
    assert result.exit_code == 0
    assert "Last checked: never" in result.output


def test_status_reports_checkpoint_pending_items_and_source_health(tmp_path):
    db = tmp_path / "pia.db"
    conn = connect(db)
    good = FakeSource("good", [make_item("good", 1, T0)])
    bad = FakeSource("bad", error=SourceError("service down"))
    run_briefing(conn, None, [good, bad], now=T0)
    conn.close()

    result = runner.invoke(app, ["--db", str(db), "status"])
    assert result.exit_code == 0
    assert "Last checked: 2026-09-20" in result.output
    assert "Not yet briefed: 0" in result.output
    assert "good" in result.output and "ok" in result.output
    assert "bad" in result.output and "service down" in result.output
