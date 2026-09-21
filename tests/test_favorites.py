import sqlite3
from datetime import timedelta

import pytest
from fakes import T0, make_item

from pia.db import MIGRATIONS, connect, store_items
from pia.web.favorites import ItemNotFound, add_favorite, favorite_ids, remove_favorite


@pytest.fixture
def conn():
    connection = connect(":memory:")
    store_items(connection, [make_item("a", 1, T0), make_item("a", 2, T0)], now=T0)
    yield connection
    connection.close()


def created_at(conn, item_id):
    return conn.execute("SELECT created_at FROM favorites WHERE item_id = ?", (item_id,)).fetchone()[0]


def test_migration_v3_upgrades_a_v2_database_like_the_real_one_without_touching_its_data(tmp_path):
    assert len(MIGRATIONS) >= 3
    path = tmp_path / "v2.db"
    old = sqlite3.connect(path)
    old.executescript("BEGIN;\n" + MIGRATIONS[0] + "\n" + MIGRATIONS[1] + "\nPRAGMA user_version = 2;\nCOMMIT;")
    old.execute(
        "INSERT INTO items (source, external_id, canonical_url, url, title, discovered_at, status) "
        "VALUES ('hn', '1', 'https://x.test/1', 'https://x.test/1', 'Real item', '2026-09-20T00:00:00+00:00', 'briefed')"
    )
    old.commit()
    old.close()

    upgraded = connect(path)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert upgraded.execute("SELECT title FROM items").fetchone()[0] == "Real item"
    assert upgraded.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 0
    upgraded.close()


def test_adding_a_favorite_records_it(conn):
    add_favorite(conn, 1, now=T0)
    assert favorite_ids(conn) == {1}


def test_adding_twice_is_idempotent_and_keeps_the_original_timestamp(conn):
    add_favorite(conn, 1, now=T0)
    add_favorite(conn, 1, now=T0 + timedelta(days=1))
    assert conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 1
    assert created_at(conn, 1) == "2026-09-20T12:00:00+00:00"


def test_removing_is_idempotent(conn):
    add_favorite(conn, 1, now=T0)
    remove_favorite(conn, 1)
    remove_favorite(conn, 1)  # already gone: still fine, same end state
    assert favorite_ids(conn) == set()


def test_removing_a_favorite_that_was_never_added_is_fine(conn):
    remove_favorite(conn, 2)
    assert favorite_ids(conn) == set()


def test_an_unknown_item_is_an_error_not_a_dangling_row(conn):
    with pytest.raises(ItemNotFound):
        add_favorite(conn, 999, now=T0)
    with pytest.raises(ItemNotFound):
        remove_favorite(conn, 999)
    assert conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 0


def test_favorites_persist_in_the_database_file_across_connections(tmp_path):
    path = tmp_path / "pia.db"
    first = connect(path)
    store_items(first, [make_item("a", 1, T0)], now=T0)
    add_favorite(first, 1, now=T0)
    first.close()

    second = connect(path)  # "restart the app"
    assert favorite_ids(second) == {1}
    second.close()


def test_deleting_an_item_removes_its_favorite_row(conn):
    add_favorite(conn, 1, now=T0)
    conn.execute("DELETE FROM items WHERE id = 1")
    assert conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 0
