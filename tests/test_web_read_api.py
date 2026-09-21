"""The read side of the web API, exercised against a database built by the REAL pipeline."""

import hashlib
from datetime import timedelta

import pytest
from fakes import T0, FakeSource, SmartLLM, make_item
from fastapi.testclient import TestClient

from pia.briefing.curate import make_curator
from pia.db import connect
from pia.pipeline import run_briefing
from pia.web.app import create_app
from pia.web.favorites import add_favorite

CATEGORIES = ["ai", "software", "research"]


def build_db(path):
    """Briefing 1 shows 12 of 35 items (4 headlines + 8 extras); briefing 2 is an empty 'nothing new' check."""
    conn = connect(path)
    a_items = [make_item("a", n, T0 - timedelta(hours=n), content=f"Article text {n}") for n in range(30)]
    b_items = [make_item("b", n, T0 - timedelta(hours=n), signals={"points": 100 + n}) for n in range(5)]  # title only
    scores = {f"a item {n}": (CATEGORIES[n % 3], 5 - (n % 4)) for n in range(30)}
    scores.update({f"b item {n}": ("software", 3) for n in range(5)})
    llm = SmartLLM(scores=scores)
    sources = [FakeSource("a", a_items), FakeSource("b", b_items)]
    run_briefing(conn, None, sources, T0, prepare=make_curator(llm))
    run_briefing(conn, None, sources, T0 + timedelta(days=1), prepare=make_curator(llm))
    conn.close()


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "pia.db"
    build_db(db)
    client = TestClient(create_app(db), base_url="http://127.0.0.1")
    return client, db


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- status ----------


def test_status_reports_the_checkpoint_counts_and_sources(env):
    client, _ = env
    body = client.get("/api/status").json()
    assert body["last_checked"].startswith("2026-09-21")
    assert body["latest_briefing_id"] == 2
    assert body["counts"] == {"library": 12, "skipped": 23, "favorites": 0}
    assert body["sources"] == ["a", "b"]


# ---------- briefings ----------


def test_briefings_are_listed_newest_first_with_counts(env):
    client, _ = env
    rows = client.get("/api/briefings").json()
    assert [r["id"] for r in rows] == [2, 1]
    assert (rows[1]["shown"], rows[1]["skipped"]) == (12, 23)
    assert (rows[0]["shown"], rows[0]["skipped"]) == (0, 0)


def test_current_is_the_latest_briefing_that_had_content_not_the_empty_check(env):
    client, _ = env
    body = client.get("/api/briefings/current").json()
    assert body["id"] == 1 and body["parsed"] is True
    assert len(body["headlines"]) == 4
    assert sum(len(s["items"]) for s in body["sections"]) == 8
    assert {s["category"] for s in body["sections"]} <= set(CATEGORIES)
    assert all(s["label"] for s in body["sections"])


def test_briefing_entries_are_linked_back_to_real_item_ids_with_favorite_state(env):
    client, db = env
    headline = client.get("/api/briefings/current").json()["headlines"][0]
    assert isinstance(headline["item_id"], int) and headline["favorite"] is False
    assert headline["explanation"].startswith("Explained ") and headline["why_it_matters"].startswith("Because ")

    conn = connect(db)
    add_favorite(conn, headline["item_id"], now=T0)
    conn.close()
    again = client.get("/api/briefings/current").json()["headlines"][0]
    assert again["favorite"] is True


def test_an_empty_check_is_described_as_empty(env):
    client, _ = env
    body = client.get("/api/briefings/2").json()
    assert body["parsed"] is True and body["headlines"] == []
    assert body["empty_message"] == "Nothing new since your last check."


def test_a_briefing_in_an_unknown_format_falls_back_to_raw_markdown(env):
    client, db = env
    conn = connect(db)
    conn.execute(
        "INSERT INTO briefings (covers_from, covers_until, created_at, rendered_md) "
        "VALUES (NULL, '2026-09-22T00:00:00+00:00', '2026-09-22T00:00:00+00:00', '# Some future layout')"
    )
    conn.commit()
    conn.close()
    body = client.get("/api/briefings/3").json()
    assert body["parsed"] is False and body["markdown"] == "# Some future layout"
    assert body["headlines"] == [] and body["sections"] == []


def test_unknown_briefings_are_404(env):
    client, _ = env
    assert client.get("/api/briefings/99").status_code == 404


def test_current_is_404_when_nothing_has_ever_run(tmp_path):
    client = TestClient(create_app(tmp_path / "empty.db"), base_url="http://127.0.0.1")
    assert client.get("/api/briefings/current").status_code == 404
    assert client.get("/api/briefings").json() == []
    assert client.get("/api/status").json()["last_checked"] is None


# ---------- library ----------


def items(client, **params):
    response = client.get("/api/items", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_library_holds_everything_that_was_shown_and_nothing_that_was_not(env):
    client, _ = env
    page = items(client)
    assert page["total"] == 12 and len(page["items"]) == 12
    assert {i["status"] for i in page["items"]} == {"briefed"}
    first = page["items"][0]
    for key in ("id", "title", "url", "source", "seen_on", "category", "summary", "briefing_id", "briefed_at", "favorite"):
        assert key in first
    assert first["briefing_id"] == 1 and first["favorite"] is False


def test_the_toggle_adds_items_the_briefing_skipped_so_history_is_fully_reachable(env):
    client, _ = env
    page = items(client, include_skipped="true")
    assert page["total"] == 35
    assert {i["status"] for i in page["items"]} == {"briefed", "skipped"} or page["limit"] < 35


def test_title_only_items_have_no_summary(env):
    client, _ = env
    page = items(client, include_skipped="true", source="b", limit=100)
    assert page["total"] == 5 and all(i["summary"] in (None, "") for i in page["items"])


def test_pagination(env):
    client, _ = env
    first = items(client, limit=5, offset=0)
    second = items(client, limit=5, offset=5)
    assert first["total"] == second["total"] == 12
    assert len(first["items"]) == len(second["items"]) == 5
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    assert len(items(client, limit=5, offset=10)["items"]) == 2


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"category": "made_up"}])
def test_bad_query_parameters_are_rejected_with_422(env, params):
    client, _ = env
    assert client.get("/api/items", params=params).status_code == 422


def test_search_is_case_insensitive_and_treats_wildcards_literally(env):
    client, _ = env
    assert items(client, q="A ITEM 1", include_skipped="true")["total"] >= 1
    assert items(client, q="%", include_skipped="true")["total"] == 0  # not a wildcard
    assert items(client, q="_", include_skipped="true")["total"] == 0
    assert items(client, q="no such thing")["total"] == 0


def test_filter_by_category_and_source(env):
    client, _ = env
    software = items(client, category="software", include_skipped="true", limit=100)
    assert software["total"] > 0 and {i["category"] for i in software["items"]} == {"software"}
    only_b = items(client, source="b", include_skipped="true", limit=100)
    assert {i["source"] for i in only_b["items"]} == {"b"}


def test_favorites_view_shows_favorites_whatever_their_status_newest_favorite_first(env):
    client, db = env
    conn = connect(db)
    skipped_id = conn.execute("SELECT id FROM items WHERE status = 'skipped' LIMIT 1").fetchone()[0]
    shown_id = conn.execute("SELECT id FROM items WHERE status = 'briefed' LIMIT 1").fetchone()[0]
    add_favorite(conn, shown_id, now=T0)
    add_favorite(conn, skipped_id, now=T0 + timedelta(hours=1))
    conn.close()

    assert skipped_id not in {i["id"] for i in items(client, limit=100)["items"]}  # not in the default library
    favorites = items(client, favorite="true")
    assert [i["id"] for i in favorites["items"]] == [skipped_id, shown_id]
    assert all(i["favorite"] and i["favorited_at"] for i in favorites["items"])


# ---------- security posture and read-only guarantee ----------


def test_read_endpoints_never_modify_the_database(env):
    client, db = env
    before = digest(db)
    for path in ("/api/status", "/api/briefings", "/api/briefings/current", "/api/briefings/1", "/api/items", "/"):
        client.get(path)
    client.get("/api/items", params={"include_skipped": "true", "q": "item"})
    assert digest(db) == before


def test_requests_with_a_foreign_host_header_are_refused(env):
    client, _ = env
    assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/status", headers={"host": "localhost:8765"}).status_code == 200


def test_no_cors_headers_are_ever_sent(env):
    client, _ = env
    response = client.get("/api/status", headers={"origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_security_headers_are_present(env):
    client, _ = env
    headers = client.get("/api/status").headers
    assert headers["x-content-type-options"] == "nosniff"
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
