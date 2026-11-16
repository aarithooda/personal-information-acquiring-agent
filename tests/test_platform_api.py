"""The HTTP API: reading, state, feedback, interests, sources, opportunities, devices, auth and the security
rules, plus static checks on the web app's files."""

import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pia.intel import opportunities
from pia.intel.usage import ModelConfig
from pia.server import auth
from pia.server.app import STATIC_DIR, create_app
from pia.worker import Services
from tests.platform_helpers import NOW, add_item, add_source, make_db

BASE = "http://127.0.0.1"


@pytest.fixture
def world(tmp_path):
    """A database with a few items, the app over it, and a client. No models, no network."""
    path = tmp_path / "platform.db"
    conn = make_db(path)
    feed = add_source(conn, "AI Lab", domains=("ai",))
    maths = add_source(conn, "Maths blog", domains=("mathematics",), url="https://m.example/feed")
    ids = {
        "agents": add_item(conn, "AI agents in practice", source=feed, content="A long discussion of agent design."),
        "llm": add_item(conn, "LLM evaluation is hard", source=feed, content="On benchmarks."),
        "primes": add_item(conn, "A proof about prime numbers", source=maths, content="We prove a theorem."),
        "hack": add_item(conn, "Agents hackathon", source=add_source(conn, "Contests", "json_api", domains=("hackathons",), purpose="opportunities", url=None, config={"preset": "codeforces"}),
                         kind="opportunity", signals={"deadline": "2026-12-01", "organisation": "Acme"}),
    }
    opportunities.extract_opportunity(conn, ids["hack"], NOW)
    services = Services(http=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))), config=ModelConfig())
    app = create_app(path, services, clock=lambda: NOW)
    return {"conn": conn, "ids": ids, "client": TestClient(app, base_url=BASE), "app": app, "path": path, "services": services}


def test_bootstrap_and_home_describe_the_library(world):
    client = world["client"]
    boot = client.get("/api/bootstrap").json()
    assert {d["slug"]: d["unread"] for d in boot["domains"]}["ai"] == 2 and boot["counts"]["items"] == 4 and boot["counts"]["opportunities"] == 1
    assert boot["agent"] == {"running": False, "background": False, "last_run": None, "notes": [], "models": False} and boot["auth_required"] is False
    home = client.get("/api/home").json()
    assert len(home["featured"]) == 4 and home["deadlines"][0]["programme"] == "Agents hackathon" and home["deadlines"][0]["days_left"] == 16


def test_feeds_filter_by_space_state_kind_and_search(world):
    client, ids = world["client"], world["ids"]
    titles = lambda **params: [i["title"] for i in client.get("/api/items", params=params).json()["items"]]  # noqa: E731
    assert set(titles(domain="ai")) == {"AI agents in practice", "LLM evaluation is hard"} and titles(domain="mathematics") == ["A proof about prime numbers"]
    assert titles(kind="opportunity") == ["Agents hackathon"] and titles(q="prime") == ["A proof about prime numbers"] and titles(q="pri") == ["A proof about prime numbers"]
    assert titles(q='"; DROP TABLE items; --') == [] and titles(q="!!!") == []  # anything typed is safe to search for
    client.post(f"/api/items/{ids['llm']}/state", json={"read": True})
    assert titles(domain="ai", state="unread") == ["AI agents in practice"] and titles(domain="ai", state="read") == ["LLM evaluation is hard"]
    page = client.get("/api/items", params={"limit": 2}).json()
    assert len(page["items"]) == 2 and page["total"] == 4 and page["has_more"] is True
    assert client.get("/api/items", params={"limit": 2, "offset": 2}).json()["has_more"] is False


def test_item_states_persist_and_are_idempotent(world):
    client, item_id = world["client"], world["ids"]["agents"]
    assert client.post(f"/api/items/{item_id}/state", json={"saved": True}).json()["saved_at"]
    assert client.post(f"/api/items/{item_id}/state", json={"saved": True}).status_code == 200
    again = TestClient(create_app(world["path"], world["services"], clock=lambda: NOW), base_url=BASE)  # a second session, e.g. another device
    detail = again.get(f"/api/items/{item_id}").json()
    assert detail["saved"] and detail["read"] and [i["id"] for i in again.get("/api/items", params={"state": "saved"}).json()["items"]] == [item_id]
    client.post(f"/api/items/{item_id}/state", json={"dismissed": True})
    assert item_id not in [i["id"] for i in client.get("/api/items").json()["items"]]
    assert item_id in [i["id"] for i in client.get("/api/items", params={"state": "dismissed"}).json()["items"]]
    client.post(f"/api/items/{item_id}/state", json={"dismissed": False, "saved": False})
    assert item_id in [i["id"] for i in client.get("/api/items").json()["items"]]
    assert client.post("/api/items/424242/state", json={"read": True}).status_code == 404 and client.get("/api/items/424242").status_code == 404


def test_opening_marks_read_once_and_bulk_read_works(world):
    client, ids = world["client"], world["ids"]
    client.post(f"/api/items/{ids['agents']}/open")
    client.post(f"/api/items/{ids['agents']}/open")
    assert world["conn"].execute("SELECT COUNT(*) FROM events WHERE kind = 'open'").fetchone()[0] == 1
    assert client.post("/api/items/read", json={"ids": [ids["llm"], ids["primes"]]}).json() == {"read": 2}
    assert {d["slug"]: d["unread"] for d in client.get("/api/bootstrap").json()["domains"]}["ai"] == 0


def test_feedback_changes_the_profile_and_can_be_undone(world):
    client, item_id = world["client"], world["ids"]["agents"]
    assert client.post(f"/api/items/{item_id}/feedback", json={"kind": "not_relevant", "reason": "too basic"}).json()["feedback"] == [{"kind": "not_relevant", "reason": "too basic"}]
    learned = {t["name"]: t["affinity"] for t in client.get("/api/profile").json()["topics"]}
    assert learned["AI agents"] < 0
    assert client.delete(f"/api/items/{item_id}/feedback/not_relevant").json()["feedback"] == []
    assert client.post(f"/api/items/{item_id}/feedback", json={"kind": "bogus"}).status_code == 422


def test_rating_and_following_topics_reorders_the_feed(world):
    client, conn = world["client"], world["conn"]
    topic = conn.execute("SELECT id FROM topics WHERE name = 'Number theory'").fetchone()[0]
    assert client.put(f"/api/topics/{topic}", json={"level": 3}).status_code == 200
    assert client.get("/api/items", params={"view": "important"}).json()["items"][0]["title"] == "A proof about prime numbers"
    assert client.put(f"/api/topics/{topic}", json={"level": 7}).status_code == 422
    assert client.get("/api/bootstrap").json()["onboarding"]["rated"] == 1
    new = client.post("/api/topics", json={"name": "benchmarks"}).json()["id"]
    timeline = client.get(f"/api/topics/{new}").json()
    assert [i["title"] for i in timeline["items"]] == ["LLM evaluation is hard"]  # found in what PIA already holds
    assert client.put("/api/domains/ai", json={"weight": 0}).status_code == 200 and client.put("/api/domains/ai", json={"weight": 9}).status_code == 422
    assert "AI agents in practice" not in [i["title"] for i in client.get("/api/home").json()["featured"]]  # a hidden space stays off Home
    assert client.put("/api/domains/nope", json={"weight": 1}).status_code == 404


def test_opportunities_are_listed_tracked_and_counted(world):
    client, item_id = world["client"], world["ids"]["hack"]
    listing = client.get("/api/opportunities").json()
    assert listing["counts"]["open"] == 1 and listing["opportunities"][0]["state"] == "open" and listing["opportunities"][0]["organisation"] == "Acme"
    assert client.patch(f"/api/opportunities/{item_id}", json={"status": "applied", "notes": "Sent on the 15th"}).status_code == 200
    tracked = client.get("/api/opportunities", params={"show": "tracked"}).json()
    assert tracked["opportunities"][0]["status"] == "applied" and tracked["counts"]["status_applied"] == 1 and tracked["opportunities"][0]["saved"]
    assert client.patch(f"/api/opportunities/{item_id}", json={"status": "hired"}).status_code == 422
    assert client.patch("/api/opportunities/424242", json={"status": "applied"}).status_code == 404
    org = client.post("/api/organisations", json={"name": "Acme Research"}).json()["id"]
    assert client.get("/api/opportunities").json()["organisations"][0]["name"] == "Acme Research"
    assert client.delete(f"/api/organisations/{org}").status_code == 200


def test_sources_can_be_listed_paused_approved_and_removed(world, monkeypatch):
    from pia.acquire import safefetch

    monkeypatch.setattr(safefetch, "system_resolver", lambda host: ["93.184.216.34"])  # the mock network answers 404 to everything
    client, conn = world["client"], world["conn"]
    candidate = add_source(conn, "Suggested", status="candidate", url="https://s.example/feed")["id"]
    listed = {s["name"]: s for s in client.get("/api/sources").json()["sources"]}
    assert listed["AI Lab"]["item_count"] == 2 and listed["AI Lab"]["health"] == "pending" and listed["Suggested"]["status"] == "candidate"
    assert client.patch(f"/api/sources/{candidate}", json={"status": "active"}).status_code == 200
    assert client.patch(f"/api/sources/{candidate}", json={"status": "sideways"}).status_code == 422
    assert client.delete(f"/api/sources/{candidate}").status_code == 200 and client.delete(f"/api/sources/{candidate}").status_code == 404
    assert client.post("/api/sources/inspect", json={"url": "https://nothing.example"}).status_code == 422  # unreachable: explained, not a crash


def test_briefs_need_a_model_and_say_so(world):
    client, item_id = world["client"], world["ids"]["agents"]
    assert client.get(f"/api/items/{item_id}").json()["can_brief"] is False
    response = client.post(f"/api/items/{item_id}/brief")
    assert response.status_code == 503 and "GROQ_API_KEY" in response.json()["detail"]


def test_devices_use_bearer_tokens_and_notification_settings_round_trip(world):
    client = world["client"]
    token = client.post("/api/devices", json={"name": "Laptop"}).json()["token"]
    assert client.get("/api/device/pending").status_code == 401 and client.get("/api/device/pending", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/device/pending", headers={"Authorization": f"Bearer {token}"}).json() == {"notifications": []}
    assert client.post("/api/device/ack", json={"ids": []}, headers={"Authorization": f"Bearer {token}"}).status_code == 200
    saved = client.put("/api/notifications/settings", json={"thresholds": {"important": 0.7}, "quiet_hours": {"enabled": False}}).json()
    assert saved["thresholds"]["important"] == 0.7 and saved["quiet_hours"]["enabled"] is False and saved["quiet_hours"]["start"] == "23:00"
    overview = client.get("/api/notifications").json()
    assert overview["devices"][0]["name"] == "Laptop" and "token" not in str(overview["devices"])  # the token is shown once, at creation


def test_the_system_page_reports_health_and_can_run_a_cycle(world):
    client = world["client"]
    system = client.get("/api/system").json()
    assert system["sources"] == {"pending": 3} and system["usage"]["total_usd"] == 0 and system["background"] is False
    ran = client.post("/api/system/run", json={}).json()  # no background agent: the cycle runs here, with the mock network failing every source
    assert ran["background"] is False and ran["stats"]["acquire"]["failed"] == 3
    assert client.get("/api/system").json()["runs"][0]["kind"] == "cycle"


def test_no_endpoint_ever_returns_an_api_key_and_the_system_page_reports_groq_only(world):
    secret = "gsk_THIS_IS_A_TEST_SECRET_0123456789abcdef"
    world["services"].groq_key = secret
    client, item_id = world["client"], world["ids"]["agents"]
    world["conn"].execute("UPDATE items SET fit = 0.9 WHERE id = ?", (item_id,))
    world["conn"].commit()
    for path in ("/", "/api/session", "/api/bootstrap", "/api/home", "/api/items", f"/api/items/{item_id}", "/api/profile", "/api/sources",
                 "/api/opportunities", "/api/notifications", "/api/system"):
        response = client.get(path)
        assert response.status_code == 200 and secret not in response.text and "gsk_" not in response.text, path
    system = client.get("/api/system").json()
    assert system["jev"] is False and system["providers"] == ["groq"]
    assert client.get(f"/api/items/{item_id}").json()["fit"] is None  # a stored Jev judgement is not shown while Jev is off
    assert client.get("/api/bootstrap").json()["agent"]["models"] is True


# ---------- security ----------


def test_every_response_carries_the_security_headers_and_api_answers_are_not_cached(world):
    for path in ("/", "/api/bootstrap", "/static/app.css"):
        headers = world["client"].get(path).headers
        assert "script-src 'self'" in headers["content-security-policy"] and "'unsafe-inline'" not in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
    assert world["client"].get("/api/bootstrap").headers["cache-control"] == "no-store"
    assert world["client"].get("/static/app.css").headers["cache-control"] == "no-cache"


def test_a_local_app_answers_only_to_local_host_names(world):
    """A web page you visit must not be able to drive a local app by pointing its own domain at 127.0.0.1."""
    assert world["client"].get("/api/bootstrap", headers={"host": "evil.example"}).status_code == 400
    assert world["client"].get("/api/bootstrap", headers={"host": "localhost:8765"}).status_code == 200


def test_state_changing_requests_from_another_origin_are_refused(world):
    item_id = world["ids"]["agents"]
    crossed = world["client"].post(f"/api/items/{item_id}/state", json={"dismissed": True}, headers={"origin": "https://evil.example"})
    assert crossed.status_code == 403 and world["conn"].execute("SELECT dismissed_at FROM items WHERE id = ?", (item_id,)).fetchone()[0] is None
    assert world["client"].post(f"/api/items/{item_id}/state", json={"read": True}, headers={"origin": "http://127.0.0.1"}).status_code == 200


def test_with_a_password_everything_needs_a_session(world):
    conn, client = world["conn"], world["client"]
    with pytest.raises(ValueError):
        auth.set_password(conn, "short")
    auth.set_password(conn, "correct horse battery")
    assert client.get("/api/session").json() == {"auth_required": True, "signed_in": False}
    for method, path in (("get", "/api/bootstrap"), ("get", "/api/items"), ("post", "/api/system/run"), ("get", "/api/sources"), ("post", "/api/devices")):
        assert getattr(client, method)(path).status_code == 401, path
    assert client.get("/").status_code == 200 and client.get("/api/health").json() == {"ok": True}  # the shell loads; the data does not
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    login = client.post("/api/login", json={"password": "correct horse battery"})
    assert login.status_code == 200 and "httponly" in login.headers["set-cookie"].lower() and "samesite=strict" in login.headers["set-cookie"].lower()
    assert client.get("/api/bootstrap").status_code == 200
    assert "correct horse" not in str(conn.execute("SELECT * FROM settings").fetchall()[0][:])  # stored hashed
    client.post("/api/logout")
    assert client.get("/api/bootstrap").status_code == 401
    token = TestClient(world["app"], base_url=BASE)
    assert token.get("/api/bootstrap", cookies={auth.COOKIE: "forged"}).status_code == 401


def test_devices_still_work_with_a_password_and_guessing_is_throttled(world):
    conn, client = world["conn"], world["client"]
    from pia.notify import engine

    _, token = engine.register_device(conn, "Laptop", NOW)
    auth.set_password(conn, "correct horse battery")
    assert client.get("/api/device/pending", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    for _ in range(auth.MAX_FAILURES):
        assert client.post("/api/login", json={"password": "guess"}).status_code == 401
    assert client.post("/api/login", json={"password": "correct horse battery"}).status_code == 429  # locked out, even with the right password


def test_passwords_are_verified_in_constant_time_shape_and_sessions_expire():
    stored = auth.hash_password("a long password")
    assert stored.startswith("scrypt$") and auth.verify_password("a long password", stored) and not auth.verify_password("a long passwor", stored)
    assert not auth.verify_password("x", "garbage") and not auth.verify_password("x", "md5$00$00")
    conn = make_db()
    token = auth.create_session(conn, NOW)
    from datetime import timedelta

    assert auth.session_valid(conn, token, NOW) and not auth.session_valid(conn, token, NOW + timedelta(days=auth.SESSION_DAYS + 1)) and not auth.session_valid(conn, None, NOW)
    throttle = auth.LoginThrottle(clock=lambda: 0.0)
    for _ in range(auth.MAX_FAILURES):
        throttle.failed()
    assert throttle.locked()


# ---------- the web app's files ----------

SCRIPTS = sorted((STATIC_DIR / "js").glob("*.js"))


def test_the_page_has_no_inline_code_and_loads_nothing_from_elsewhere():
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", page) and not re.search(r"\son\w+\s*=", page) and "style=" not in page
    assert not re.search(r'(src|href)="https?://', page) and "<style" not in page
    assets = re.findall(r'(?:src|href)="(/static/[^"]+)"', page)
    assert assets and all((STATIC_DIR / asset.removeprefix("/static/")).is_file() for asset in assets)


def test_scripts_never_render_text_as_html_and_only_link_checked_urls():
    assert len(SCRIPTS) >= 5
    for script in SCRIPTS:
        text = script.read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "dangerouslySetInnerHTML", "document.write", "eval(", "insertAdjacentHTML"):
            assert banned not in text, f"{script.name} uses {banned}"
        assert not re.search(r"https?://(?!www\.w3\.org)[a-z]", text), f"{script.name} refers to an outside address"
    ui = (STATIC_DIR / "js" / "ui.js").read_text(encoding="utf-8") + (STATIC_DIR / "js" / "item.js").read_text(encoding="utf-8")
    assert "safeUrl(item.url)" in ui and 'rel="noopener noreferrer"' in ui  # item links pass through the http(s)-only check


def test_fonts_and_the_vendored_library_are_local_files():
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    fonts = re.findall(r'url\("(/static/fonts/[^"]+)"\)', css)
    assert len(fonts) == 3 and all((STATIC_DIR / font.removeprefix("/static/")).stat().st_size > 10_000 for font in fonts)
    assert "@import" not in css and not re.search(r"url\(\"?https?:", css)
    assert (STATIC_DIR / "vendor" / "preact-htm.js").stat().st_size > 5_000
    assert Path(STATIC_DIR / "icon.svg").is_file()
