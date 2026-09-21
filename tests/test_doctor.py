import hashlib
import sqlite3

import httpx
import pytest
from fakes import T0, FakeSource, make_item

from pia.db import MIGRATIONS, connect
from pia.doctor import run_checks
from pia.http import SourceError
from pia.pipeline import run_briefing

KEY = "gsk_test_1234567890abcdef"
CONFIG = """
[[source]]
type = "hn"
min_points = 100

[[source]]
type = "rss"
name = "blog"
url = "https://x.test/feed"
"""


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".env").write_text(f"GROQ_API_KEY={KEY}\n")
    config = root / "sources.toml"
    config.write_text(CONFIG)
    db = root / "data" / "pia.db"
    return root, config, db


def check(setup, **overrides):
    root, config, db = setup
    args = dict(db_path=db, config_path=config, root=root, environ={}, online=False)
    args.update(overrides)
    return {c.name: c for c in run_checks(**args)}


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_db(db, briefed=True):
    conn = connect(db)
    run_briefing(conn, None, [FakeSource("a", [make_item("a", 1, T0)] if briefed else [])], now=T0)
    conn.close()


def test_a_healthy_setup_passes_every_offline_check(setup):
    make_db(setup[2])
    checks = check(setup)
    assert {c.status for c in checks.values()} == {"ok"}
    assert f"schema v{len(MIGRATIONS)}" in checks["Database"].detail
    assert "2 sources" in checks["Sources config"].detail


def test_the_api_key_value_never_appears_in_any_output(setup):
    make_db(setup[2])
    for c in check(setup).values():
        assert KEY not in c.name + c.detail
        assert "gsk_test" not in c.detail


def test_the_key_check_says_where_the_key_was_found(setup):
    root = setup[0]
    (root / ".env").rename(root / ".env.txt")
    detail = check(setup)["API key"].detail
    assert ".env.txt" in detail and "found" in detail


def test_a_missing_key_fails_with_instructions(setup):
    (setup[0] / ".env").unlink()
    result = check(setup)["API key"]
    assert result.status == "fail" and ".env" in result.detail


def test_a_broken_sources_config_fails_but_other_checks_still_run(setup):
    setup[1].write_text('[[source]]\ntype = "twitter"\n')
    checks = check(setup)
    assert checks["Sources config"].status == "fail" and "twitter" in checks["Sources config"].detail
    assert "API key" in checks and "Database" in checks


def test_a_missing_database_is_fine_and_is_not_created(setup):
    db = setup[2]
    result = check(setup)["Database"]
    assert result.status == "ok" and "not created yet" in result.detail
    assert not db.exists() and not db.parent.exists()


def test_an_old_schema_warns_that_it_will_upgrade_and_is_not_touched(setup):
    db = setup[2]
    db.parent.mkdir()
    old = sqlite3.connect(db)
    old.executescript(f"BEGIN;\n{MIGRATIONS[0]}\nPRAGMA user_version = 1;\nCOMMIT;")
    old.close()
    before = digest(db)
    result = check(setup)["Database"]
    assert result.status == "warn" and "upgraded" in result.detail
    assert digest(db) == before


def test_a_database_from_a_newer_version_is_a_failure(setup):
    db = setup[2]
    db.parent.mkdir()
    newer = sqlite3.connect(db)
    newer.execute("PRAGMA user_version = 99")
    newer.close()
    result = check(setup)["Database"]
    assert result.status == "fail" and "newer" in result.detail


def test_a_corrupt_database_file_is_reported_not_a_crash(setup):
    db = setup[2]
    db.parent.mkdir()
    db.write_bytes(b"this is definitely not a sqlite database" * 50)
    result = check(setup)["Database"]
    assert result.status == "fail" and "cannot be read" in result.detail


def test_offline_checks_never_modify_the_database(setup):
    make_db(setup[2])
    before = digest(setup[2])
    check(setup)
    assert digest(setup[2]) == before


# ---------- --online: real network calls in production, fakes here ----------


def groq_client(status=200):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"data": []})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_online_checks_groq_and_each_source_without_storing_anything(setup):
    make_db(setup[2])
    before = digest(setup[2])
    client, seen = groq_client()
    sources = [FakeSource("good", [make_item("good", 1, T0)]), FakeSource("down", error=SourceError("HTTP 503"))]
    checks = check(setup, online=True, client=client, sources=sources, now=T0)

    assert checks["Groq API"].status == "ok"
    assert seen[0].url.path.endswith("/models") and seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert checks["Source: good"].status == "ok" and "1 item" in checks["Source: good"].detail
    assert checks["Source: down"].status == "fail" and "503" in checks["Source: down"].detail
    assert digest(setup[2]) == before
    assert all(KEY not in c.detail for c in checks.values())


def test_a_rejected_key_is_diagnosed(setup):
    client, _ = groq_client(status=401)
    result = check(setup, online=True, client=client, sources=[], now=T0)["Groq API"]
    assert result.status == "fail" and "rejected" in result.detail and KEY not in result.detail


def test_without_a_key_the_groq_check_is_skipped_not_crashed(setup):
    (setup[0] / ".env").unlink()
    client, seen = groq_client()
    result = check(setup, online=True, client=client, sources=[], now=T0)["Groq API"]
    assert result.status == "fail" and seen == []


def test_offline_mode_makes_no_network_calls(setup):
    client, seen = groq_client()
    check(setup, online=False, client=client, sources=[FakeSource("s")])
    assert seen == []
