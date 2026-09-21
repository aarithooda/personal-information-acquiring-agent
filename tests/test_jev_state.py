"""Persistence and ranking changes that let Jev's continuous relevance ride on the existing pipeline."""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from fakes import T0, make_item

from pia.briefing.rank import rank_items
from pia.db import MIGRATIONS, connect, store_items
from pia.state import enriched_items, save_enrichments


@pytest.fixture
def conn():
    connection = connect(":memory:")
    store_items(connection, [make_item("a", 1, T0), make_item("a", 2, T0)], now=T0)
    yield connection
    connection.close()


def result(category="ai", importance=3, summary="", **extra):
    return SimpleNamespace(category=category, importance=importance, summary=summary, **extra)


def test_migration_v4_adds_nullable_columns_and_keeps_existing_enrichments(tmp_path):
    assert len(MIGRATIONS) >= 4
    path = tmp_path / "v3.db"
    old = sqlite3.connect(path)
    script = "\n".join(MIGRATIONS[:3])
    old.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = 3;\nCOMMIT;")
    old.execute(
        "INSERT INTO items (source, external_id, canonical_url, url, title, discovered_at) "
        "VALUES ('hn','1','https://x.test/1','https://x.test/1','T','2026-09-20T00:00:00+00:00')"
    )
    old.execute(
        "INSERT INTO enrichments (item_id, model, prompt_version, category, importance, summary, created_at) "
        "VALUES (1, 'gpt-oss', 'stage1-v2', 'ai', 4, 'kept', '2026-09-20T00:00:00+00:00')"
    )
    old.commit()
    old.close()

    upgraded = connect(path)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    row = upgraded.execute("SELECT summary, relevance, details, profile_hash FROM enrichments").fetchone()
    assert (row["summary"], row["relevance"], row["details"], row["profile_hash"]) == ("kept", None, None, None)
    upgraded.close()


def test_jev_results_store_relevance_raw_details_and_the_profile_hash(conn):
    details = {"answers": {"buildable": 0.9}, "usage": {"input_tokens": 100}}
    save_enrichments(
        conn,
        {1: result(relevance=0.82, details=details)},
        model="jev-1.13.0",
        prompt_version="jev-triage-v1",
        now=T0,
        profile_hash="abc123def456",
    )
    row = conn.execute("SELECT * FROM enrichments").fetchone()
    assert (row["model"], row["prompt_version"], row["profile_hash"]) == ("jev-1.13.0", "jev-triage-v1", "abc123def456")
    assert row["relevance"] == pytest.approx(0.82)
    assert json.loads(row["details"]) == details  # raw answers survive, so the formula can be re-derived offline
    assert conn.execute("SELECT status FROM items WHERE id = 1").fetchone()[0] == "enriched"


def test_results_without_the_new_fields_still_save_as_before(conn):
    save_enrichments(conn, {1: result("software", 2, "A summary.")}, model="gpt-oss", prompt_version="stage1-v2", now=T0)
    row = conn.execute("SELECT summary, relevance, details, profile_hash FROM enrichments").fetchone()
    assert (row["summary"], row["relevance"], row["details"], row["profile_hash"]) == ("A summary.", None, None, None)


def test_enriched_items_expose_relevance_to_the_ranker(conn):
    save_enrichments(conn, {1: result(relevance=0.7), 2: result()}, model="m", prompt_version="v", now=T0)
    by_title = {r["title"]: r["relevance"] for r in enriched_items(conn)}
    assert by_title == {"a item 1": pytest.approx(0.7), "a item 2": None}


# ---------- ranking ----------


def ranked_row(id_, importance, relevance=None, published="2026-09-18T00:00:00+00:00"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT ? AS id, ? AS importance, ? AS relevance, '{}' AS signals, ? AS published_at, ? AS discovered_at",
        (id_, importance, relevance, published, published),
    ).fetchone()


def test_relevance_gives_finer_resolution_than_the_integer_importance_it_maps_to():
    same_bucket = [ranked_row(1, 4, 0.71), ranked_row(2, 4, 0.86), ranked_row(3, 4, 0.78)]
    assert [r["id"] for r in rank_items(same_bucket)] == [2, 3, 1]


def test_rows_without_relevance_still_rank_by_importance_and_can_mix_with_jev_rows():
    rows = [ranked_row(1, 2), ranked_row(2, 4, 0.9), ranked_row(3, 5), ranked_row(4, 3, 0.4)]
    # relevance 0.9 -> base 4.6, importance 5 -> 5, relevance 0.4 -> 2.6, importance 2 -> 2
    assert [r["id"] for r in rank_items(rows)] == [3, 2, 4, 1]


def test_rows_from_older_databases_without_a_relevance_column_still_rank():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    rows = [
        conn.execute("SELECT ? AS id, ? AS importance, '{}' AS signals, '2026-09-18' AS published_at, '2026-09-18' AS discovered_at", (i, imp)).fetchone()
        for i, imp in ((1, 2), (2, 5))
    ]
    assert [r["id"] for r in rank_items(rows)] == [2, 1]
