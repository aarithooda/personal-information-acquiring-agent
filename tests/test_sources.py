"""Adapter tests run against real responses saved in tests/fixtures.

Parsing is pure (text in, RawItems out) so it needs no network. `fetch` is tested
through httpx.MockTransport, which exercises the real request-building code.
"""

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from pia.http import SourceError
from pia.models import RawItem
from pia.sources.arxiv import ArxivSource, parse_arxiv
from pia.sources.base import strip_html, within_window
from pia.sources.github import GitHubSource, parse_github
from pia.sources.hackernews import HackerNewsSource, parse_hn
from pia.sources.hf_papers import HFPapersSource, parse_hf_papers
from pia.sources.rss import RssSource, parse_rss

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def client_returning(body: str, seen: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


WIDE = (utc(2026, 9, 1), utc(2026, 9, 21))


# ---------- shared helpers ----------


def test_strip_html_removes_tags_and_entities_and_collapses_whitespace():
    assert strip_html("<p>Hello &amp;   <b>world</b></p>\n\n") == "Hello & world"


def test_within_window_is_inclusive_and_keeps_undated_items():
    def item(ts):
        return RawItem(source="s", external_id=str(ts), url="https://x.test/1", title="t", published_at=ts)

    since, until = utc(2026, 9, 10), utc(2026, 9, 12)
    kept = within_window(
        [item(utc(2026, 9, 9)), item(since), item(utc(2026, 9, 11)), item(until), item(utc(2026, 9, 13)), item(None)],
        since,
        until,
    )
    assert [i.published_at for i in kept] == [since, utc(2026, 9, 11), until, None]


# ---------- arXiv ----------


def test_parse_arxiv_extracts_fields_from_real_response():
    items = parse_arxiv(fixture("arxiv.xml"))
    assert len(items) == 5
    first = items[0]
    assert first.source == "arxiv"
    assert first.external_id == "2609.20822"
    assert first.url == "https://arxiv.org/abs/2609.20822v1"
    assert first.title == "Coding Agents with an Obstacle-Aware Harness for Safe Robot Manipulation"
    assert first.published_at == utc(2026, 9, 17, 17, 59, 58)
    assert first.content.startswith("Coding agents have emerged")
    assert "\n" not in first.content
    assert first.signals["categories"][:2] == ["cs.RO", "cs.AI"]


def test_arxiv_fetch_queries_the_requested_window_and_categories():
    seen: list[httpx.Request] = []
    source = ArxivSource(categories=["cs.AI", "cs.MA"], max_results=50)
    items = source.fetch(client_returning(fixture("arxiv.xml"), seen), utc(2026, 9, 17), utc(2026, 9, 20, 23, 59))
    query = seen[0].url.params["search_query"]
    assert "cat:cs.AI" in query and "cat:cs.MA" in query
    assert "submittedDate:[202609170000 TO 202609202359]" in query
    assert seen[0].url.params["max_results"] == "50"
    assert len(items) == 5


def test_arxiv_fetch_raises_source_error_on_garbage():
    with pytest.raises(SourceError):
        ArxivSource(categories=["cs.AI"]).fetch(client_returning("<html>rate limited</html> nope"), *WIDE)


# ---------- Hacker News ----------


def test_parse_hn_extracts_fields_and_signals():
    items = parse_hn(fixture("hn.json"))
    assert len(items) == 5
    third = items[3]
    assert third.source == "hn"
    assert third.external_id == "49775499"
    assert third.published_at == utc(2026, 9, 20, 13, 9, 25)
    assert third.signals["points"] == 249
    assert third.signals["hn_url"] == "https://news.ycombinator.com/item?id=49775499"


def test_parse_hn_falls_back_to_discussion_url_when_story_has_no_url():
    payload = '{"hits": [{"objectID": "1", "title": "Ask HN: Anything?", "url": null, "points": 90, "num_comments": 3, "created_at_i": 1789000000}]}'
    (item,) = parse_hn(payload)
    assert item.url == "https://news.ycombinator.com/item?id=1"


def test_parse_hn_skips_hits_without_a_title():
    payload = '{"hits": [{"objectID": "1", "url": "https://a.test", "created_at_i": 1789000000}, {"objectID": "2", "title": "ok", "url": "https://b.test", "created_at_i": 1789000000}]}'
    assert [i.external_id for i in parse_hn(payload)] == ["2"]


def test_hn_fetch_filters_by_time_and_points_server_side():
    seen: list[httpx.Request] = []
    since, until = utc(2026, 9, 17), utc(2026, 9, 20)
    HackerNewsSource(min_points=80).fetch(client_returning(fixture("hn.json"), seen), since, until)
    filters = seen[0].url.params["numericFilters"]
    assert f"created_at_i>{int(since.timestamp())}" in filters
    assert f"created_at_i<={int(until.timestamp())}" in filters
    assert "points>=80" in filters


def test_hn_fetch_raises_source_error_on_malformed_json():
    with pytest.raises(SourceError):
        HackerNewsSource().fetch(client_returning("{not json"), *WIDE)


# ---------- GitHub ----------


def test_parse_github_extracts_fields_and_signals():
    items = parse_github(fixture("github.json"))
    assert len(items) == 5
    first = items[0]
    assert first.source == "github"
    assert first.external_id == "1374561793"
    assert first.url == "https://github.com/yibie/awesome-jev"
    assert first.title == "yibie/awesome-jev"
    assert first.published_at == utc(2026, 9, 17, 14, 23, 21)
    assert first.signals["stars"] == 547
    assert "curated list" in first.content


def test_parse_github_tolerates_null_description():
    payload = '{"items": [{"id": 1, "full_name": "a/b", "html_url": "https://github.com/a/b", "description": null, "stargazers_count": 5, "created_at": "2026-09-17T00:00:00Z", "topics": []}]}'
    (item,) = parse_github(payload)
    assert item.content is None


def test_github_fetch_builds_a_created_since_star_filtered_query():
    seen: list[httpx.Request] = []
    GitHubSource(query="llm OR agent", min_stars=25).fetch(
        client_returning(fixture("github.json"), seen), utc(2026, 9, 13), utc(2026, 9, 20)
    )
    q = seen[0].url.params["q"]
    assert "llm OR agent" in q and "created:>=2026-09-13" in q and "stars:>=25" in q
    assert seen[0].url.params["sort"] == "stars"


# ---------- Hugging Face Daily Papers ----------


def test_parse_hf_papers_points_at_the_arxiv_url_so_dedup_can_merge():
    items = parse_hf_papers(fixture("hf_daily.json"))
    assert len(items) == 5
    first = items[0]
    assert first.source == "hf_papers"
    assert first.external_id == "2609.17496"
    assert first.url == "https://arxiv.org/abs/2609.17496"
    assert first.title == "Verifiable Social Reasoning for LLM Assistants"
    assert first.signals["upvotes"] == 47
    assert first.signals["featured_at"] == "2026-09-18T00:00:00+00:00"


def test_hf_fetch_filters_by_upvotes_and_featured_date():
    source = HFPapersSource(min_upvotes=30)
    items = source.fetch(client_returning(fixture("hf_daily.json")), utc(2026, 9, 17), utc(2026, 9, 20))
    assert [i.external_id for i in items] == ["2609.17496", "2609.19656"]  # 47 and 43 upvotes
    assert source.fetch(client_returning(fixture("hf_daily.json")), utc(2026, 9, 19), utc(2026, 9, 20)) == []


# ---------- RSS ----------


def test_parse_rss_extracts_fields_and_uses_link_as_url():
    items = parse_rss(fixture("quanta.xml"), source="quanta")
    assert len(items) == 5
    first = items[0]
    assert first.source == "quanta"
    assert first.url == "https://www.quantamagazine.org/mathematicians-build-long-awaited-graph-sandwich-20260918/"
    assert first.title == "Mathematicians Build Long-Awaited Graph Sandwich"
    assert first.published_at == utc(2026, 9, 18, 13, 55, 50)
    assert first.content.startswith("The proof of a decades-old conjecture")
    assert "<" not in first.content


def test_parse_rss_allows_feeds_without_summaries():
    items = parse_rss(fixture("hf_blog.xml"), source="hf_blog")
    assert items[0].content is None
    assert items[0].title == "Your Agent Aced the Task. Will It Do It Again?"


def test_rss_fetch_returns_only_items_inside_the_window():
    source = RssSource(name="deepmind", url="https://deepmind.google/blog/rss.xml")
    items = source.fetch(client_returning(fixture("deepmind.xml")), utc(2026, 9, 5), utc(2026, 9, 20))
    assert len(items) == 2  # Sep 15 and Sep 8; the Sep 3 post is out of window


def test_rss_fetch_raises_source_error_on_garbage():
    with pytest.raises(SourceError):
        RssSource(name="x", url="https://x.test/feed").fetch(client_returning("<html>oops</html> nope"), *WIDE)


def test_a_feed_with_zero_entries_is_valid_and_returns_no_items():
    assert parse_rss('<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>', source="x") == []


# ---------- google_ai (config/sources.toml; uses the generic RSS adapter, no dedicated code) ----------


def test_parse_google_ai_extracts_fields_and_strips_html_from_the_summary():
    items = parse_rss(fixture("google_ai.xml"), source="google_ai")
    assert len(items) == 3
    first = items[0]
    assert first.source == "google_ai"
    assert first.url == "https://blog.google/innovation-and-ai/technology/ai/gemini-3-8-rollout/"
    assert first.title == "Gemini 3.8 is rolling out across Search, Workspace and the Gemini app"
    assert first.published_at == utc(2026, 9, 22, 16, 0, 0)
    assert "<" not in first.content
    assert "Gemini 3.8 brings faster responses" in first.content


def test_parse_google_ai_allows_an_entry_with_no_description():
    items = parse_rss(fixture("google_ai.xml"), source="google_ai")
    assert items[2].title == "How the Gemini API is helping developers ship faster"
    assert items[2].content is None


def test_google_ai_fetch_returns_only_items_inside_the_window():
    source = RssSource(name="google_ai", url="https://blog.google/innovation-and-ai/technology/ai/rss/")
    items = source.fetch(client_returning(fixture("google_ai.xml")), utc(2026, 9, 20), utc(2026, 9, 23))
    assert [i.url for i in items] == ["https://blog.google/innovation-and-ai/technology/ai/gemini-3-8-rollout/"]
