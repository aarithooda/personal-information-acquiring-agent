"""The write side: marking and unmarking favorites over HTTP, and the guarantees around it."""

import sqlite3
from datetime import timedelta

import pytest
from fakes import T0, FakeSource, SmartLLM, make_item
from fastapi.testclient import TestClient
from test_web_read_api import build_db

from pia.briefing.curate import make_curator
from pia.db import connect
from pia.pipeline import run_briefing
from pia.web.app import create_app


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "pia.db"
    build_db(db)
    return TestClient(create_app(db), base_url="http://127.0.0.1"), db


def favorite_rows(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT item_id, created_at FROM favorites ORDER BY item_id").fetchall()
    finally:
        conn.close()


def a_shown_item_id(client) -> int:
    return client.get("/api/items").json()["items"][0]["id"]


def test_put_marks_a_favorite_and_it_shows_up_everywhere(env):
    client, _ = env
    item_id = a_shown_item_id(client)
    response = client.put(f"/api/items/{item_id}/favorite")
    assert response.status_code == 200 and response.json() == {"item_id": item_id, "favorite": True}

    favorites = client.get("/api/items", params={"favorite": "true"}).json()
    assert [i["id"] for i in favorites["items"]] == [item_id]
    library_row = next(i for i in client.get("/api/items", params={"limit": 100}).json()["items"] if i["id"] == item_id)
    assert library_row["favorite"] is True
    assert client.get("/api/status").json()["counts"]["favorites"] == 1


def test_put_twice_is_the_same_as_once_and_keeps_the_first_timestamp(env):
    client, db = env
    item_id = a_shown_item_id(client)
    client.put(f"/api/items/{item_id}/favorite")
    first = favorite_rows(db)
    assert client.put(f"/api/items/{item_id}/favorite").status_code == 200
    assert favorite_rows(db) == first


def test_delete_unmarks_and_is_repeatable(env):
    client, db = env
    item_id = a_shown_item_id(client)
    client.put(f"/api/items/{item_id}/favorite")
    assert client.delete(f"/api/items/{item_id}/favorite").json() == {"item_id": item_id, "favorite": False}
    assert client.delete(f"/api/items/{item_id}/favorite").status_code == 200  # already gone: same end state
    assert favorite_rows(db) == []
    assert client.get("/api/items", params={"favorite": "true"}).json()["total"] == 0


def test_unknown_items_are_404_and_bad_ids_are_422(env):
    client, db = env
    assert client.put("/api/items/99999/favorite").status_code == 404
    assert client.delete("/api/items/99999/favorite").status_code == 404
    assert client.put("/api/items/not-a-number/favorite").status_code == 422
    assert favorite_rows(db) == []


def test_favorites_persist_in_sqlite_across_an_app_restart(env):
    client, db = env
    item_id = a_shown_item_id(client)
    client.put(f"/api/items/{item_id}/favorite")

    assert [row[0] for row in favorite_rows(db)] == [item_id]  # really in the database file
    restarted = TestClient(create_app(db), base_url="http://127.0.0.1")  # a brand-new app instance
    page = restarted.get("/api/items", params={"favorite": "true"}).json()
    assert [i["id"] for i in page["items"]] == [item_id]


def test_favoriting_a_skipped_item_found_through_the_history_toggle_works(env):
    client, _ = env
    skipped = next(i for i in client.get("/api/items", params={"include_skipped": "true", "limit": 100}).json()["items"] if i["status"] == "skipped")
    assert client.put(f"/api/items/{skipped['id']}/favorite").status_code == 200
    assert [i["id"] for i in client.get("/api/items", params={"favorite": "true"}).json()["items"]] == [skipped["id"]]


def test_the_write_endpoints_change_only_the_favorites_table(env):
    client, db = env

    def snapshot():
        conn = sqlite3.connect(db)
        try:
            return {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in ("items", "enrichments", "briefings", "fetch_runs")}
        finally:
            conn.close()

    before = snapshot()
    item_id = a_shown_item_id(client)
    client.put(f"/api/items/{item_id}/favorite")
    client.delete(f"/api/items/{item_id}/favorite")
    client.put(f"/api/items/{item_id}/favorite")
    assert snapshot() == before


def test_favorites_survive_new_pipeline_runs_the_core_does_not_know_about_them(env):
    client, db = env
    item_id = a_shown_item_id(client)
    client.put(f"/api/items/{item_id}/favorite")

    conn = connect(db)
    fresh = FakeSource("c", [make_item("c", n, T0 + timedelta(days=1)) for n in range(5)])
    run_briefing(conn, None, [fresh], T0 + timedelta(days=2), prepare=make_curator(SmartLLM()))
    conn.close()

    assert [row[0] for row in favorite_rows(db)] == [item_id]
    assert client.get("/api/items", params={"favorite": "true"}).json()["total"] == 1


# ---------- what a hostile web page could try against a local server ----------


def test_state_cannot_be_changed_with_a_plain_link_or_image_tag(env):
    client, db = env
    item_id = a_shown_item_id(client)
    assert client.get(f"/api/items/{item_id}/favorite").status_code == 405  # GET is what <a> and <img> send
    assert client.post(f"/api/items/{item_id}/favorite").status_code == 405
    assert favorite_rows(db) == []


def test_a_cross_origin_preflight_gets_no_permission(env):
    client, _ = env
    response = client.options(
        "/api/items/1/favorite",
        headers={"origin": "https://evil.example", "access-control-request-method": "PUT"},
    )
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-methods" not in response.headers


def test_a_dns_rebinding_style_request_with_a_foreign_host_changes_nothing(env):
    client, db = env
    item_id = a_shown_item_id(client)
    response = client.put(f"/api/items/{item_id}/favorite", headers={"host": "evil.example"})
    assert response.status_code == 400
    assert favorite_rows(db) == []
