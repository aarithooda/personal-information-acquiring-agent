import pytest
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


def test_enrich_without_an_api_key_explains_what_to_do(tmp_path, monkeypatch):
    import pia.cli

    def no_key():
        raise pia.cli.ConfigError("GROQ_API_KEY not found. Put it in .env")

    monkeypatch.setattr(pia.cli, "get_groq_api_key", no_key)
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "enrich"])
    assert result.exit_code == 1
    assert "GROQ_API_KEY" in result.output


def test_enrich_runs_stage_one_and_shows_what_the_model_decided(tmp_path, monkeypatch):
    import pia.cli
    from fakes import FakeLLM, batch_response, entry
    from pia.db import store_items

    db = tmp_path / "pia.db"
    conn = connect(db)
    store_items(conn, [make_item("hn", 1, T0, content="Details.")], now=T0)
    conn.close()

    monkeypatch.setattr(pia.cli, "make_llm", lambda client: FakeLLM(batch_response(entry(0, "ai", 4, "A big model."))))
    result = runner.invoke(app, ["--db", str(db), "enrich"])
    assert result.exit_code == 0
    assert "Enriched 1" in result.output
    assert "A big model." in result.output and "hn item 1" in result.output


def test_default_command_prints_a_briefing_and_records_the_checkpoint(tmp_path, monkeypatch):
    import pia.cli
    from fakes import SmartLLM

    db = tmp_path / "pia.db"
    source = FakeSource("a", [make_item("a", n, T0) for n in range(3)])
    monkeypatch.setattr(pia.cli, "load_sources", lambda path: [source])
    monkeypatch.setattr(pia.cli, "make_llm", lambda client: SmartLLM())

    result = runner.invoke(app, ["--db", str(db)])
    assert result.exit_code == 0
    assert "Worth knowing" in result.output
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM briefings").fetchone()[0] == 1


def test_default_command_without_a_key_exits_cleanly_and_records_nothing(tmp_path, monkeypatch):
    import pia.cli

    def no_key(client):
        raise pia.cli.ConfigError("GROQ_API_KEY not found")

    monkeypatch.setattr(pia.cli, "load_sources", lambda path: [FakeSource("a", [make_item("a", 1, T0)])])
    monkeypatch.setattr(pia.cli, "make_llm", no_key)
    db = tmp_path / "pia.db"
    result = runner.invoke(app, ["--db", str(db)])
    assert result.exit_code == 1 and "GROQ_API_KEY" in result.output
    assert connect(db).execute("SELECT COUNT(*) FROM briefings").fetchone()[0] == 0


def test_default_command_reports_total_triage_failure_and_records_nothing(tmp_path, monkeypatch):
    import pia.cli
    from fakes import SmartLLM
    from pia.llm.client import LLMError

    monkeypatch.setattr(pia.cli, "load_sources", lambda path: [FakeSource("a", [make_item("a", 1, T0)])])
    monkeypatch.setattr(pia.cli, "make_llm", lambda client: SmartLLM(triage_error=LLMError("down")))
    db = tmp_path / "pia.db"
    result = runner.invoke(app, ["--db", str(db)])
    assert result.exit_code == 1 and "nothing was recorded" in result.output.lower()
    assert connect(db).execute("SELECT COUNT(*) FROM briefings").fetchone()[0] == 0


def test_status_on_a_fresh_database(tmp_path):
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "status"])
    assert result.exit_code == 0
    assert "Last checked: never" in result.output


def test_status_counts_items_the_system_gave_up_on(tmp_path):
    db = tmp_path / "pia.db"
    conn = connect(db)
    run_briefing(conn, None, [FakeSource("a", [make_item("a", 1, T0), make_item("a", 2, T0)])], now=T0)
    conn.execute("UPDATE items SET status = 'failed', briefing_id = NULL WHERE id = 1")
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["--db", str(db), "status"])
    assert "Could not be processed (given up on): 1" in result.output
    assert "Not yet briefed: 0" in result.output  # given-up items are not "waiting" for a briefing


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


# ---------- read-only inspection commands: history / show / status ----------


def _db_with_two_briefings(tmp_path):
    """Briefing 1 shows two items; briefing 2 ('nothing new') is the newest but empty."""
    from datetime import timedelta

    db = tmp_path / "pia.db"
    conn = connect(db)
    source = FakeSource("a", [make_item("a", 1, T0), make_item("a", 2, T0)])
    run_briefing(conn, None, [source], now=T0)
    run_briefing(conn, None, [source], now=T0 + timedelta(days=1))
    conn.close()
    return db


def _digest(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_history_lists_every_check_newest_first(tmp_path):
    db = _db_with_two_briefings(tmp_path)
    result = runner.invoke(app, ["--db", str(db), "history"])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip().startswith("#")]
    assert lines[0].strip().startswith("#2") and "showed 0" in lines[0]
    assert lines[1].strip().startswith("#1") and "showed 2" in lines[1]


def test_show_defaults_to_the_latest_briefing_that_had_content(tmp_path):
    db = _db_with_two_briefings(tmp_path)
    result = runner.invoke(app, ["--db", str(db), "show", "--raw"])
    assert result.exit_code == 0
    assert "a item 1" in result.output and "0 new item(s)" not in result.output


def test_show_can_pick_a_briefing_by_number(tmp_path):
    db = _db_with_two_briefings(tmp_path)
    result = runner.invoke(app, ["--db", str(db), "show", "2", "--raw"])
    assert result.exit_code == 0 and "0 new item(s)" in result.output


def test_show_without_raw_prints_a_header_naming_the_briefing(tmp_path):
    db = _db_with_two_briefings(tmp_path)
    result = runner.invoke(app, ["--db", str(db), "show"])
    assert result.exit_code == 0 and "Briefing #1" in result.output


def test_show_an_unknown_number_explains_how_to_find_the_right_one(tmp_path):
    db = _db_with_two_briefings(tmp_path)
    result = runner.invoke(app, ["--db", str(db), "show", "99"])
    assert result.exit_code == 1 and "pia history" in result.output


def test_inspection_commands_never_create_or_change_the_database(tmp_path):
    missing = tmp_path / "nothing-here" / "pia.db"
    for command in (["history"], ["show"], ["status"]):
        runner.invoke(app, ["--db", str(missing), *command])
        assert not missing.exists() and not missing.parent.exists(), command

    db = _db_with_two_briefings(tmp_path)
    before = _digest(db)
    for command in (["history"], ["show"], ["show", "2"], ["status"]):
        result = runner.invoke(app, ["--db", str(db), *command])
        assert result.exit_code == 0, (command, result.output)
    assert _digest(db) == before


def test_history_and_show_explain_an_empty_installation_instead_of_crashing(tmp_path):
    missing = str(tmp_path / "pia.db")
    history = runner.invoke(app, ["--db", missing, "history"])
    assert history.exit_code == 0 and "No briefings yet" in history.output
    show = runner.invoke(app, ["--db", missing, "show"])
    assert show.exit_code == 1 and "No briefings yet" in show.output


def test_status_does_not_upgrade_an_old_database(tmp_path):
    import sqlite3

    from pia.db import MIGRATIONS

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(f"BEGIN;\n{MIGRATIONS[0]}\nPRAGMA user_version = 1;\nCOMMIT;")
    old.close()
    before = _digest(path)
    result = runner.invoke(app, ["--db", str(path), "status"])
    assert result.exit_code == 0
    assert _digest(path) == before


# ---------- doctor ----------


def _patch_checks(monkeypatch, *checks):
    import pia.cli
    from pia.doctor import Check

    seen = {}

    def fake_run_checks(**kwargs):
        seen.update(kwargs)
        return [Check(*c) for c in checks]

    monkeypatch.setattr(pia.cli, "run_checks", fake_run_checks)
    return seen


def test_doctor_all_good_exits_zero(tmp_path, monkeypatch):
    _patch_checks(monkeypatch, ("Python", "ok", "3.11.7"), ("Database", "ok", "schema v2"))
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "doctor"])
    assert result.exit_code == 0
    assert "[ ok ] Python" in result.output and "All good" in result.output


def test_doctor_warnings_do_not_fail(tmp_path, monkeypatch):
    _patch_checks(monkeypatch, ("Database", "warn", "will be upgraded"))
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "doctor"])
    assert result.exit_code == 0 and "[warn] Database" in result.output


def test_doctor_failures_exit_nonzero_and_say_how_many(tmp_path, monkeypatch):
    _patch_checks(monkeypatch, ("API key", "fail", "not found"), ("Groq API", "fail", "skipped"), ("Python", "ok", "x"))
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "doctor"])
    assert result.exit_code == 1
    assert "[FAIL] API key" in result.output and "2 problems" in result.output


def test_doctor_is_offline_unless_asked(tmp_path, monkeypatch):
    seen = _patch_checks(monkeypatch, ("Python", "ok", "x"))
    runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "doctor"])
    assert seen["online"] is False
    runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "doctor", "--online"])
    assert seen["online"] is True and seen["client"] is not None


# ---------- web ----------


class _FakeUvicorn:
    def __init__(self):
        self.calls = []

    def run(self, app, **kwargs):
        self.calls.append((app, kwargs))


def _patch_web(monkeypatch):
    import pia.cli
    from pia.web.app import create_app

    fake = _FakeUvicorn()
    opened = []
    monkeypatch.setattr(pia.cli, "_load_web", lambda: (fake, create_app))
    monkeypatch.setattr(pia.cli, "_open_later", lambda url: opened.append(url))
    return fake, opened


def test_web_starts_the_server_locally_on_the_default_port_with_the_chosen_database(tmp_path, monkeypatch):
    fake, opened = _patch_web(monkeypatch)
    db = tmp_path / "pia.db"
    result = runner.invoke(app, ["--db", str(db), "web"])
    assert result.exit_code == 0
    (served_app, kwargs), = fake.calls
    assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 8765
    assert served_app.title == "PIA" and db.exists()  # created/migrated for the chosen database
    assert "http://127.0.0.1:8765" in result.output and opened == []


def test_web_options_choose_the_port_and_can_open_the_browser(tmp_path, monkeypatch):
    fake, opened = _patch_web(monkeypatch)
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "web", "--port", "9100", "--open"])
    assert result.exit_code == 0
    assert fake.calls[0][1]["port"] == 9100 and opened == ["http://127.0.0.1:9100"]


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "example.com"])
def test_web_refuses_anything_but_a_local_address(tmp_path, monkeypatch, host):
    fake, _ = _patch_web(monkeypatch)
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "web", "--host", host])
    assert result.exit_code == 1 and "local" in result.output.lower()
    assert fake.calls == []


def test_web_explains_how_to_install_the_extras_when_they_are_missing(tmp_path, monkeypatch):
    import pia.cli

    def missing():
        raise ImportError("No module named 'uvicorn'")

    monkeypatch.setattr(pia.cli, "_load_web", missing)
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "web"])
    assert result.exit_code == 1 and 'pip install -e ".[web]"' in result.output


# ---------- Jev at Stage 1 ----------

from jevfakes import V2_PROFILE_TOML as PROFILE_TOML  # noqa: E402
from jevfakes import SyntheticJev  # noqa: E402


def _jev_setup(tmp_path, monkeypatch, *, key=True, profile=True, jev=None):
    import pia.cli
    from fakes import SmartLLM

    profile_path = tmp_path / "interests.toml"
    if profile:
        profile_path.write_text(PROFILE_TOML, encoding="utf-8")
    fake = jev or SyntheticJev()

    def make_jev(client):
        if not key:
            raise pia.cli.ConfigError("TYPESAFE_API_KEY (or JEV_API_KEY) not found.")
        return fake

    llm = SmartLLM()
    source = FakeSource("hn", [make_item("hn", n, T0, content="Text") for n in range(5)])
    monkeypatch.setattr(pia.cli, "make_jev", make_jev)
    monkeypatch.setattr(pia.cli, "make_llm", lambda client: llm)
    monkeypatch.setattr(pia.cli, "load_sources", lambda path: [source])
    return profile_path, fake, llm


def test_triage_jev_runs_jev_at_stage_one_and_the_llm_only_as_editor(tmp_path, monkeypatch):
    profile_path, fake, llm = _jev_setup(tmp_path, monkeypatch)
    db = tmp_path / "pia.db"
    result = runner.invoke(app, ["--db", str(db), "--profile", str(profile_path), "--triage", "jev"])
    assert result.exit_code == 0, result.output
    assert "Triage: Jev" in result.output and "Worth knowing" in result.output
    assert len(fake.calls) == 5 * 4 and {c["schema_name"] for c in llm.calls} == {"editor_picks"}  # four Jev requests per item
    models = {r[0] for r in connect(db).execute("SELECT model FROM enrichments")}
    assert models == {"jev-1.13.0"}


def test_triage_jev_without_a_key_fails_loudly_and_records_nothing(tmp_path, monkeypatch):
    profile_path, _, _ = _jev_setup(tmp_path, monkeypatch, key=False)
    db = tmp_path / "pia.db"
    result = runner.invoke(app, ["--db", str(db), "--profile", str(profile_path), "--triage", "jev"])
    assert result.exit_code == 1 and "TYPESAFE_API_KEY" in result.output
    assert connect(db).execute("SELECT COUNT(*) FROM briefings").fetchone()[0] == 0


def test_triage_jev_without_a_profile_says_how_to_create_one(tmp_path, monkeypatch):
    profile_path, _, _ = _jev_setup(tmp_path, monkeypatch, profile=False)
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "--profile", str(profile_path), "--triage", "jev"])
    assert result.exit_code == 1 and "interests.example.toml" in result.output


def test_an_invalid_profile_is_an_error_not_a_silent_fallback(tmp_path, monkeypatch):
    profile_path, _, _ = _jev_setup(tmp_path, monkeypatch)
    profile_path.write_text("not = = toml", encoding="utf-8")
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "--profile", str(profile_path), "--triage", "auto"])
    assert result.exit_code == 1 and "not valid TOML" in result.output


def test_auto_uses_jev_when_a_key_and_a_profile_exist(tmp_path, monkeypatch):
    profile_path, fake, _ = _jev_setup(tmp_path, monkeypatch)
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "--profile", str(profile_path), "--triage", "auto"])
    assert result.exit_code == 0 and "Triage: Jev" in result.output and len(fake.calls) == 5 * 4


def test_auto_falls_back_to_the_llm_and_says_why_when_jev_is_not_configured(tmp_path, monkeypatch):
    profile_path, fake, llm = _jev_setup(tmp_path, monkeypatch, key=False)
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "--profile", str(profile_path), "--triage", "auto"])
    assert result.exit_code == 0 and "Jev is not configured" in result.output
    assert fake.calls == [] and "triage_results" in {c["schema_name"] for c in llm.calls}


def test_triage_llm_is_exactly_the_previous_behaviour(tmp_path, monkeypatch):
    profile_path, fake, llm = _jev_setup(tmp_path, monkeypatch)
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "--profile", str(profile_path), "--triage", "llm"])
    assert result.exit_code == 0 and fake.calls == []
    assert "READER PROFILE" not in [c for c in llm.calls if c["schema_name"] == "editor_picks"][0]["system"]


def test_enrich_with_jev_shows_relevance_scores(tmp_path, monkeypatch):
    from pia.db import store_items

    profile_path, fake, _ = _jev_setup(tmp_path, monkeypatch)
    db = tmp_path / "pia.db"
    conn = connect(db)
    store_items(conn, [make_item("hn", 1, T0, content="Details.")], now=T0)
    conn.close()
    result = runner.invoke(app, ["--db", str(db), "--profile", str(profile_path), "--triage", "jev", "enrich"])
    assert result.exit_code == 0, result.output
    assert "Enriched 1" in result.output and "rel " in result.output and "hn item 1" in result.output


def test_a_profile_the_current_jev_design_cannot_use_is_an_error_with_a_reason_not_a_silent_fallback(tmp_path, monkeypatch):
    profile_path, fake, _ = _jev_setup(tmp_path, monkeypatch)
    profile_path.write_text('[low_value_information]\nusually_low_value = ["x"]\n', encoding="utf-8")
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "--profile", str(profile_path), "--triage", "jev"])
    assert result.exit_code == 1 and "priority tiers" in result.output and fake.calls == []


def test_the_run_says_which_question_set_it_used(tmp_path, monkeypatch):
    profile_path, _, _ = _jev_setup(tmp_path, monkeypatch)
    result = runner.invoke(app, ["--db", str(tmp_path / "pia.db"), "--profile", str(profile_path), "--triage", "jev"])
    assert "jev-triage-v2" in result.output


def test_a_pinned_jev_model_is_passed_to_the_client(monkeypatch):
    import httpx

    import pia.cli

    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("JEV_MODEL", "jev-1.13.0")
    with httpx.Client() as http:
        assert pia.cli.make_jev(http)._model == "jev-1.13.0"
    monkeypatch.delenv("JEV_MODEL")
    with httpx.Client() as http:
        assert pia.cli.make_jev(http)._model == "jev-latest"
