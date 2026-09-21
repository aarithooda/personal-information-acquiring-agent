"""The frozen item set: what goes in, what must never go in, and that exporting cannot hurt the real database."""

import hashlib
import json
from datetime import datetime, timezone

import pytest
from fakes import T0, make_item

from benchmarks import snapshot as snap
from benchmarks.common import BenchmarkError
from pia.db import connect, store_items
from pia.jev.triage import build_state
from pia.llm.prompts import CONTENT_CHARS
from pia.state import commit_briefing, save_enrichments

NOW = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)


class _Result:
    category, importance, summary = "ai", 5, "a summary the human must never see"
    relevance, details = 0.99, {"answers": {"secret": 1}}


@pytest.fixture
def real_db(tmp_path):
    """A database that has been through a full run: enriched by models AND shown in a briefing."""
    path = tmp_path / "pia.db"
    conn = connect(path)
    store_items(
        conn,
        [
            make_item("arxiv", 1, T0, content="An abstract about agents.", signals={"upvotes": 3}),
            make_item("hn", 2, T0, signals={"points": 250}),  # title only
            make_item("hn", 3, T0, content="   "),  # whitespace-only text counts as no text
        ],
        now=T0,
    )
    save_enrichments(conn, {1: _Result(), 2: _Result()}, model="m", prompt_version="v", now=T0, profile_hash="abc")
    commit_briefing(conn, covers_from=None, covers_until=T0, created_at=T0, rendered_md="# briefing", shown_ids=[1], skipped_ids=[2])
    conn.close()
    return path


def test_the_snapshot_holds_item_facts_and_nothing_a_model_or_a_briefing_decided(real_db, tmp_path):
    shot = snap.export_snapshot(real_db, tmp_path / "items.json", now=NOW)
    assert [i["id"] for i in shot.items] == [1, 2, 3]
    assert set(shot.items[0]) == set(snap.ITEM_FIELDS)
    text = (tmp_path / "items.json").read_text(encoding="utf-8")
    for leaked in ("a summary the human must never see", "0.99", "secret", "briefed", "skipped", "importance", "relevance", "briefing_id", "status"):
        assert leaked not in text, f"{leaked!r} leaked into the snapshot"


def test_signals_are_stored_as_data_not_as_a_json_string(real_db, tmp_path):
    shot = snap.export_snapshot(real_db, tmp_path / "items.json", now=NOW)
    assert shot.items[1]["signals"] == {"hn": {"points": 250}}


def test_export_never_modifies_the_source_database(real_db, tmp_path):
    before = hashlib.sha256(real_db.read_bytes()).hexdigest()
    snap.export_snapshot(real_db, tmp_path / "items.json", now=NOW)
    assert hashlib.sha256(real_db.read_bytes()).hexdigest() == before


def test_export_of_a_missing_database_is_a_clear_error_and_creates_nothing(tmp_path):
    with pytest.raises(BenchmarkError, match="no database"):
        snap.export_snapshot(tmp_path / "nope.db", tmp_path / "items.json", now=NOW)
    assert not (tmp_path / "nope.db").exists()


def test_a_frozen_snapshot_is_not_overwritten_unless_forced(real_db, tmp_path):
    out = tmp_path / "items.json"
    snap.export_snapshot(real_db, out, now=NOW)
    with pytest.raises(BenchmarkError, match="already exists"):
        snap.export_snapshot(real_db, out, now=NOW)
    snap.export_snapshot(real_db, out, now=NOW, force=True)


def test_snapshot_id_is_stable_and_changes_when_any_item_changes(real_db, tmp_path):
    a = snap.export_snapshot(real_db, tmp_path / "a.json", now=NOW)
    b = snap.export_snapshot(real_db, tmp_path / "b.json", now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert a.snapshot_id == b.snapshot_id  # the export time is not part of the identity
    edited = [dict(i) for i in a.items]
    edited[0]["title"] += "!"
    assert snap.compute_snapshot_id(edited) != a.snapshot_id


def test_load_round_trips_and_detects_tampering(real_db, tmp_path):
    out = tmp_path / "items.json"
    exported = snap.export_snapshot(real_db, out, now=NOW)
    assert snap.load_snapshot(out).snapshot_id == exported.snapshot_id
    data = json.loads(out.read_text(encoding="utf-8"))
    data["items"][0]["title"] = "edited after freezing"
    out.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="changed since it was frozen"):
        snap.load_snapshot(out)


def test_load_of_a_missing_snapshot_says_how_to_create_it(tmp_path):
    with pytest.raises(BenchmarkError, match="python -m benchmarks snapshot"):
        snap.load_snapshot(tmp_path / "items.json")


def test_title_only_means_no_non_whitespace_text():
    assert snap.is_title_only({"content_raw": None}) and snap.is_title_only({"content_raw": "  \n"})
    assert not snap.is_title_only({"content_raw": "text"})


def test_the_human_sees_exactly_what_jev_sees():
    item = {"source": "hn", "title": "A title", "content_raw": "x" * 900}
    view = snap.human_view(item)
    assert view == build_state({}, item)["item"]  # same source label, same title, same truncation
    assert view["source"] == "Hacker News link" and len(view["text"]) == CONTENT_CHARS
    assert "text" not in snap.human_view({"source": "hn", "title": "T", "content_raw": None})
