"""The keyless demo builder must keep working: it is the first thing a new reader is told to run."""

from fastapi.testclient import TestClient

from examples.make_demo_db import DEMO_ITEMS, main
from pia.web.app import create_app


def test_the_demo_database_builds_and_the_web_api_serves_it(tmp_path):
    db = tmp_path / "demo.db"
    assert main(["--out", str(db)]) == 0
    client = TestClient(create_app(db), base_url="http://127.0.0.1")
    briefing = client.get("/api/briefings/current").json()
    assert briefing["headlines"], "the demo briefing should contain headlines"
    assert client.get("/api/status").json()["counts"]["library"] > 0


def test_the_demo_content_is_visibly_synthetic():
    for source, title, _category, _importance, _text, _signals in DEMO_ITEMS:
        assert title.startswith("[demo]"), title


def test_the_builder_refuses_to_overwrite_without_force(tmp_path, capsys):
    db = tmp_path / "demo.db"
    db.write_bytes(b"precious")
    assert main(["--out", str(db)]) == 1
    assert db.read_bytes() == b"precious"
    assert main(["--out", str(db), "--force"]) == 0
