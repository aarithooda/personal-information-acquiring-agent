"""Unit tests for the front end's pure logic (static/logic.js), run with Node.

Separating pure logic (what to request, what to say, which links are safe) from DOM code is what makes it
testable without a browser or a JS test framework. The empty-state bug found while checking the UI in a
browser ("No favorites yet" shown while filters were hiding a favorite) is pinned by these tests.
"""

import json
import shutil
import subprocess

import pytest

from pia.web.app import STATIC_DIR

LOGIC = STATIC_DIR / "logic.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")


def js(expression: str):
    script = f"const L = require(process.env.LOGIC); console.log(JSON.stringify({expression}));"
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        env={"LOGIC": str(LOGIC), "PATH": __import__("os").environ["PATH"], "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_each_tab_starts_with_its_own_empty_filters():
    assert js("L.emptyFilters()") == {"q": "", "category": "", "source": "", "includeSkipped": False}
    assert js("L.emptyFilters() !== L.emptyFilters()") is True  # never a shared object


def test_active_filters_means_search_category_or_source_but_not_the_skipped_toggle():
    assert js("L.hasActiveFilters(L.emptyFilters())") is False
    assert js("L.hasActiveFilters({q:'x',category:'',source:'',includeSkipped:false})") is True
    assert js("L.hasActiveFilters({q:'',category:'ai',source:'',includeSkipped:false})") is True
    assert js("L.hasActiveFilters({q:'',category:'',source:'hn',includeSkipped:false})") is True
    assert js("L.hasActiveFilters({q:'',category:'',source:'',includeSkipped:true})") is False


def test_library_query_asks_for_briefed_items_by_default():
    assert js("L.buildItemsQuery('library', L.emptyFilters(), 0, 30)") == "limit=30&offset=0"


def test_library_query_carries_every_filter_and_the_skipped_toggle():
    query = js("L.buildItemsQuery('library', {q:'  agent  ',category:'ai',source:'hn',includeSkipped:true}, 60, 30)")
    assert query == "limit=30&offset=60&q=agent&category=ai&source=hn&include_skipped=true"


def test_favorites_query_asks_for_favorites_and_ignores_the_skipped_toggle():
    query = js("L.buildItemsQuery('favorites', {q:'',category:'',source:'',includeSkipped:true}, 0, 30)")
    assert query == "limit=30&offset=0&favorite=true"


def test_search_text_is_encoded_not_pasted_into_the_query():
    query = js("L.buildItemsQuery('library', {q:'a&b=c #d',category:'',source:'',includeSkipped:false}, 0, 30)")
    assert query == "limit=30&offset=0&q=a%26b%3Dc+%23d"


def test_an_empty_favorites_tab_says_so_only_when_no_filters_are_hiding_anything():
    plain = js("L.emptyMessage('favorites', L.emptyFilters())")
    filtered = js("L.emptyMessage('favorites', {q:'agent',category:'',source:'',includeSkipped:false})")
    assert plain.startswith("No favorites yet")
    assert "match" in filtered and "No favorites yet" not in filtered  # the bug seen in the browser


def test_an_empty_library_explains_how_to_widen_the_search():
    with_filters = js("L.emptyMessage('library', {q:'zzz',category:'',source:'',includeSkipped:false})")
    assert "skipped" in with_filters
    already_wide = js("L.emptyMessage('library', {q:'zzz',category:'',source:'',includeSkipped:true})")
    assert "Include items" not in already_wide and "skipped" not in already_wide
    assert "pia" in js("L.emptyMessage('library', L.emptyFilters())").lower()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a?b=1", "https://example.com/a?b=1"),
        ("http://example.com/", "http://example.com/"),
        ("javascript:alert(1)", None),
        ("JaVaScRiPt:alert(1)", None),
        ("data:text/html,<script>alert(1)</script>", None),
        ("file:///C:/secrets.txt", None),
        ("//example.com/protocol-relative", None),
        ("not a url", None),
        ("", None),
    ],
)
def test_only_http_and_https_urls_become_links(url, expected):
    assert js(f"L.safeHref({json.dumps(url)})") == expected
