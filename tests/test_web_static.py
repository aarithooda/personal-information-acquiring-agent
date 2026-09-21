"""The front end is plain files, so its safety rules are checked as rules about those files."""

import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from pia.db import connect
from pia.web.app import STATIC_DIR, create_app

APP_JS = STATIC_DIR / "app.js"
LOGIC_JS = STATIC_DIR / "logic.js"
INDEX = STATIC_DIR / "index.html"
ALL_JS = (APP_JS, LOGIC_JS)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "pia.db"), base_url="http://127.0.0.1")


def test_the_root_page_and_its_assets_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    for tab in ("#/new", "#/library", "#/favorites"):
        assert f'href="{tab}"' in page.text
    assert client.get("/static/app.css").status_code == 200
    js = client.get("/static/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]


def test_the_page_uses_no_inline_code_because_the_csp_forbids_it():
    html = INDEX.read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html), "inline <script>"
    assert not re.search(r"<style", html), "inline <style>"
    assert not re.search(r"\son[a-z]+\s*=", html), "inline event handler"
    assert not re.search(r"\sstyle\s*=", html), "inline style attribute"


def test_untrusted_text_is_never_inserted_as_html():
    for path in ALL_JS:
        js = path.read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert forbidden not in js, f"{path.name} must not use {forbidden}"
        assert 'setAttribute("style"' not in js and "setAttribute('style'" not in js  # blocked by the CSP


def test_logic_loads_before_the_app_that_depends_on_it():
    html = INDEX.read_text(encoding="utf-8")
    assert html.index("/static/logic.js") < html.index("/static/app.js")


def test_favorites_are_set_with_explicit_put_and_delete_never_a_blind_toggle():
    js = APP_JS.read_text(encoding="utf-8")
    assert '"PUT"' in js and '"DELETE"' in js
    assert "toggle" not in js.lower().replace("classlist.toggle", "")


def test_the_link_safety_rule_lives_in_the_tested_logic_module_and_app_uses_it():
    assert "function safeHref" in LOGIC_JS.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")
    assert "safeHref(url)" in app_js and "function safeHref" not in app_js  # one definition, not two


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
@pytest.mark.parametrize("path", ALL_JS, ids=lambda p: p.name)
def test_the_javascript_is_syntactically_valid(path):
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
