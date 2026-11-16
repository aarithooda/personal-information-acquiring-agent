"""The platform database: migrations, seeding, full-text index, backups and the v1 import."""

import sqlite3

import pytest

from pia.db import connect as connect_legacy
from pia.db import store_items
from pia.models import RawItem
from pia.state import save_enrichments
from pia.store import db, seed
from pia.store.legacy import import_legacy
from pia.store.schema import MIGRATIONS
from tests.platform_helpers import NOW, TAXONOMY, add_item, add_source, make_db, topic_id


def test_a_new_database_is_at_the_latest_schema_and_reopening_changes_nothing(tmp_path):
    path = tmp_path / "platform.db"
    first = db.connect(path)
    assert db.schema_version(first) == len(MIGRATIONS)
    first.execute("INSERT INTO settings (key, value) VALUES ('k', '1')")
    first.commit()
    first.close()
    again = db.connect(path)
    assert db.schema_version(again) == len(MIGRATIONS) and db.get_setting(again, "k") == 1


def test_an_older_database_is_upgraded_in_place_and_keeps_its_rows(tmp_path):
    """Build a v1-schema file by hand, then open it with today's code: data survives, the new index exists."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(f"BEGIN;{MIGRATIONS[0]}PRAGMA user_version = 1;COMMIT;")
    old.execute("INSERT INTO domains (slug, name, short) VALUES ('ai', 'AI', 'AI')")
    old.commit()
    old.close()
    assert db.needs_migration(path)
    backup = db.copy_file_backup(path, "before-upgrade")
    upgraded = db.connect(path)
    assert db.schema_version(upgraded) == len(MIGRATIONS)
    assert upgraded.execute("SELECT name FROM domains").fetchone()["name"] == "AI"
    assert upgraded.execute("SELECT 1 FROM sqlite_master WHERE name = 'idx_items_source'").fetchone()
    assert backup.is_file() and not db.needs_migration(path)


def test_a_database_newer_than_the_code_is_refused_not_guessed_at(tmp_path):
    path = tmp_path / "future.db"
    conn = db.connect(path)
    conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    conn.close()
    with pytest.raises(RuntimeError, match="newer than this code"):
        db.connect(path)


def test_seeding_is_idempotent_and_never_overwrites_an_edit():
    conn = make_db()
    conn.execute("UPDATE domains SET name = 'My AI', weight = 0.4 WHERE slug = 'ai'")
    conn.commit()
    again = seed.seed_taxonomy(conn, TAXONOMY, NOW)
    assert again == {"domains": 0, "topics": 0, "aliases_updated": 0}
    row = conn.execute("SELECT name, weight FROM domains WHERE slug = 'ai'").fetchone()
    assert (row["name"], row["weight"]) == ("My AI", 0.4)


def test_improving_a_seed_topics_vocabulary_reaches_an_existing_database_and_items_are_retagged():
    """Aliases are not editable in the app, so the config file stays their source of truth; ratings are untouched."""
    import copy

    from pia.intel.classify import retag_topics

    conn = make_db()
    loose = copy.deepcopy(TAXONOMY)
    loose["domain"][0]["topics"][0] = "AI agents | agentic | multi-agent | practice"  # a careless, generic alias
    seed.seed_taxonomy(conn, loose, NOW)
    agents = topic_id(conn, "AI agents")
    conn.execute("UPDATE topics SET level = 3, rated_at = ? WHERE id = ?", (db.iso(NOW), agents))
    wrongly_tagged = add_item(conn, "Deliberate practice for pianists", content="Nothing about software.")
    rightly_tagged = add_item(conn, "Agentic workflows in production")
    tagged = lambda item_id: bool(conn.execute("SELECT 1 FROM item_topics WHERE item_id = ? AND topic_id = ?", (item_id, agents)).fetchone())  # noqa: E731
    assert tagged(wrongly_tagged) and tagged(rightly_tagged)

    report = seed.seed_taxonomy(conn, TAXONOMY, NOW)  # the file is corrected
    assert report["aliases_updated"] == 1 and retag_topics(conn) == 1
    assert not tagged(wrongly_tagged) and tagged(rightly_tagged)
    assert conn.execute("SELECT level FROM topics WHERE id = ?", (agents,)).fetchone()[0] == 3  # the owner's rating survives


def test_the_personal_seed_sets_levels_but_never_overrides_a_rating_made_in_the_app():
    conn = make_db()
    agents = topic_id(conn, "AI agents")
    conn.execute("UPDATE topics SET level = -1, rated_at = ? WHERE id = ?", (db.iso(NOW), agents))
    report = seed.apply_personal(conn, {"topics": {"AI agents": 3, "Rust": 2, "No such topic": 2, "Number theory": 9},
                                        "domains": {"ai": 9.0}, "university": {"name": "Example Institute", "short": "EI"}, "me": {"stage": "Student"}}, NOW)
    assert conn.execute("SELECT level FROM topics WHERE id = ?", (agents,)).fetchone()[0] == -1  # the owner's own rating stands
    assert conn.execute("SELECT level FROM topics WHERE name = 'Rust'").fetchone()[0] == 2
    assert conn.execute("SELECT level FROM topics WHERE name = 'Number theory'").fetchone()[0] is None  # 9 is not a level
    assert report["unknown_topics"] == 1
    assert conn.execute("SELECT weight FROM domains WHERE slug = 'ai'").fetchone()[0] == 1.5  # clamped
    assert conn.execute("SELECT short FROM domains WHERE slug = 'university'").fetchone()[0] == "EI"
    assert db.get_setting(conn, "me") == {"stage": "Student"}


def test_catalogue_sources_start_active_or_as_candidates_and_are_not_reseeded():
    conn = make_db()
    catalog = {"source": [{"name": "Core", "kind": "rss", "url": "https://a.example/feed", "domains": ["ai"], "tier": "core"},
                          {"name": "Extra", "kind": "rss", "url": "https://b.example/feed", "domains": ["ai"], "tier": "optional", "reason": "High volume."}]}
    assert seed.seed_sources(conn, catalog, NOW) == 2
    statuses = {row["name"]: (row["status"], row["reason"]) for row in conn.execute("SELECT name, status, reason FROM sources")}
    assert statuses == {"Core": ("active", ""), "Extra": ("candidate", "High volume.")}
    conn.execute("UPDATE sources SET status = 'paused' WHERE name = 'Core'")
    assert seed.seed_sources(conn, catalog, NOW) == 0
    assert conn.execute("SELECT status FROM sources WHERE name = 'Core'").fetchone()[0] == "paused"


def test_the_committed_catalogue_and_taxonomy_load_and_agree_with_each_other():
    taxonomy, catalog = seed.load_toml(seed.TAXONOMY_FILE), seed.load_toml(seed.CATALOG_FILE)
    slugs = {domain["slug"] for domain in taxonomy["domain"]}
    assert len(slugs) == 15
    names = [source["name"] for source in catalog["source"]]
    assert len(names) == len(set(names)) and len(names) > 100
    for source in catalog["source"]:
        assert set(source["domains"]) <= slugs, source["name"]
        assert source["kind"] in ("rss", "arxiv", "hn", "github", "hf_papers", "json_api"), source["name"]
        assert source["kind"] != "rss" or source["url"].startswith("http"), source["name"]


def test_the_full_text_index_follows_inserts_updates_and_deletes():
    conn = make_db()
    item_id = add_item(conn, "Sparse autoencoders explained", content="A note on interpretability.")
    search = lambda term: [row[0] for row in conn.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH ?", (term,))]  # noqa: E731
    assert search("autoencoders") == [item_id] and search("interpretability") == [item_id]
    conn.execute("UPDATE items SET summary = 'About dictionary learning.' WHERE id = ?", (item_id,))
    assert search("dictionary") == [item_id]
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    assert search("autoencoders") == []


def test_a_run_cut_short_is_reported_as_interrupted_not_left_looking_in_progress():
    conn = make_db()
    finished = db.start_run(conn, "cycle", NOW)
    db.finish_run(conn, finished, NOW, stats={"classified": 1})
    db.start_run(conn, "cycle", NOW)  # the window was closed here
    assert db.close_interrupted_runs(conn, NOW) == 1 and db.close_interrupted_runs(conn, NOW) == 0
    rows = conn.execute("SELECT ok, error FROM runs ORDER BY id").fetchall()
    assert rows[0]["ok"] == 1 and rows[1]["ok"] == 0 and "Interrupted" in rows[1]["error"]


def test_backup_writes_a_readable_copy_and_prunes_old_ones(tmp_path):
    path = tmp_path / "platform.db"
    conn = make_db(path)
    add_item(conn, "Kept in the backup")
    from datetime import timedelta

    targets = [db.backup(conn, path, NOW + timedelta(minutes=n), keep=2) for n in range(3)]
    kept = sorted((tmp_path / "backups").glob("platform-*.db"))
    assert kept == targets[1:]
    copy = sqlite3.connect(kept[-1])
    assert copy.execute("SELECT title FROM items").fetchone()[0] == "Kept in the backup"


def test_importing_v1_copies_items_judgements_and_favourites_and_leaves_the_old_file_untouched(tmp_path):
    legacy_path = tmp_path / "pia.db"
    old = connect_legacy(legacy_path)
    store_items(old, [RawItem(source="hn", external_id="1", url="https://example.com/a", title="An LLM agent story", signals={"points": 300}),
                      RawItem(source="arxiv", external_id="2", url="https://arxiv.org/abs/2609.00001", title="A proof about primes", content="We prove a theorem.")], NOW)

    class Triage:
        category, importance, summary, relevance, details = "ai", 4, "A story.", None, None

    class JevTriage:
        category, importance, summary, relevance, details = "research", 3, "", 0.7, {"answers": {}}

    save_enrichments(old, {1: Triage()}, model="openai/gpt-oss-20b", prompt_version="stage1-v2", now=NOW)
    save_enrichments(old, {2: JevTriage()}, model="jev-1.13.0", prompt_version="jev-triage-v2", now=NOW, profile_hash="abc")
    old.execute("INSERT INTO favorites (item_id, created_at) VALUES (2, ?)", (db.iso(NOW),))
    old.commit()
    old.close()
    before = legacy_path.read_bytes()

    conn = make_db()
    add_source(conn, "Hacker News", "hn", general=True, domains=("software",))
    report = import_legacy(conn, legacy_path, NOW)
    assert (report["items"], report["judgements"], report["saved"]) == (2, 2, 1)
    story = conn.execute("SELECT * FROM items WHERE title = 'An LLM agent story'").fetchone()
    paper = conn.execute("SELECT * FROM items WHERE title = 'A proof about primes'").fetchone()
    assert story["model_importance"] == 0.75 and story["stage"] == "assessed" and story["source_name"] == "Hacker News"
    assert paper["fit"] == 0.7 and paper["model_importance"] is None and paper["saved_at"] and paper["kind"] == "paper"
    assert story["notified"] == 2  # an import must never produce a burst of notifications
    assert import_legacy(conn, legacy_path, NOW)["already_present"] == 2  # safe to run twice
    assert legacy_path.read_bytes() == before
    assert import_legacy(conn, tmp_path / "missing.db", NOW) == {"found": False}
