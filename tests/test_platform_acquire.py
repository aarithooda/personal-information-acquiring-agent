"""Acquisition: storing and merging items, cursors and failure handling, adapters, the guarded fetcher,
feed discovery, source evaluation and source management."""

import json
from datetime import timedelta

import httpx
import pytest

from pia.acquire import adapters, discovery, manage, runner
from pia.acquire.adapters import PageSource, detect_job_board, devpost_deadline, parse_codeforces, parse_devpost, parse_greenhouse
from pia.acquire.feeds import find_feed, parse_entries, preview_text
from pia.acquire.safefetch import FetchFailed, FetchRefused, RobotsCache, check_url, guarded_get, is_public_address
from pia.acquire.textextract import extract
from pia.http import SourceError
from pia.models import RawItem
from pia.store import db
from tests.platform_helpers import NOW, add_item, add_source, item, make_db

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731 - a resolver that says every host is on the public internet

FEED = """<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<title>Example Lab</title><link>https://lab.example</link>
<item><title>A new LLM agent benchmark</title><link>https://lab.example/a</link><guid>a</guid><pubDate>Sat, 14 Nov 2026 10:00:00 GMT</pubDate>
<description>Short.</description><content:encoded><![CDATA[<p>The full text is much longer than the summary and says what the benchmark measures.</p>]]></content:encoded></item>
<item><title>Number theory seminar notes</title><link>https://lab.example/b</link><guid>b</guid><pubDate>Fri, 13 Nov 2026 10:00:00 GMT</pubDate><description>On prime numbers.</description></item>
<item><title>Old news</title><link>https://lab.example/c</link><guid>c</guid><pubDate>Mon, 05 Jan 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def raw(n, **extra):
    fields = {"source": "s", "external_id": str(n), "url": f"https://example.org/{n}", "title": f"Item {n}", **extra}
    return RawItem(**fields)


class FakeAdapter:
    def __init__(self, items=None, error=None, state=None):
        self.items, self.error, self.windows = items or [], error, []
        if state is not None:
            self.state = state

    def fetch(self, client, since, until):
        self.windows.append((since, until))
        if self.error:
            raise self.error
        return self.items


def use_adapters(monkeypatch, by_name):
    monkeypatch.setattr(runner, "build_adapter", lambda row: by_name[row["name"]])


# ---------- storing ----------


def test_a_repeat_sighting_merges_into_one_item_and_records_both_sources():
    conn = make_db()
    first, second = add_source(conn, "One"), add_source(conn, "Two", url="https://two.example/feed")
    assert runner.upsert_item(conn, first, raw(1, url="https://example.org/a?utm_source=x", signals={"points": 10}), NOW) == "inserted"
    assert runner.upsert_item(conn, second, raw(2, url="https://www.example.org/a/", content="The text.", signals={"stars": 5, "points": None}), NOW) == "merged"
    rows = conn.execute("SELECT * FROM items").fetchall()
    assert len(rows) == 1 and rows[0]["source_name"] == "One"  # the first source to report it keeps the attribution
    assert json.loads(rows[0]["signals"]) == {"points": 10, "stars": 5}
    assert rows[0]["content"] == "The text."  # empty text is filled in by a later sighting...
    runner.upsert_item(conn, first, raw(3, url="https://example.org/a", content="Different text."), NOW)
    assert conn.execute("SELECT content FROM items").fetchone()[0] == "The text."  # ...but existing text is never overwritten
    assert [r["source_name"] for r in conn.execute("SELECT source_name FROM item_sources ORDER BY source_name")] == ["One", "Two"]


def test_a_date_in_the_future_cannot_pin_an_item_to_the_top_of_latest():
    conn = make_db()
    runner.upsert_item(conn, add_source(conn), raw(1, published_at=NOW + timedelta(days=400)), NOW)
    row = conn.execute("SELECT published_at, sort_at FROM items").fetchone()
    assert row["sort_at"] == db.iso(NOW) and row["published_at"] > row["sort_at"]


def test_an_item_with_an_unusable_url_is_rejected_and_counted_not_fatal(monkeypatch):
    conn = make_db()
    add_source(conn, "A")
    use_adapters(monkeypatch, {"A": FakeAdapter([raw(1), raw(2, url="ftp://nope/x"), raw(3)])})
    (result,) = runner.acquire(conn, None, NOW)
    assert (result.inserted, result.rejected, result.ok) == (2, 1, True)


# ---------- cursors, schedule and failures ----------


def test_one_failing_source_does_not_affect_the_others_and_backs_off(monkeypatch):
    conn = make_db()
    add_source(conn, "Good")
    add_source(conn, "Down", url="https://down.example/feed")
    add_source(conn, "Buggy", url="https://bug.example/feed")
    use_adapters(monkeypatch, {"Good": FakeAdapter([raw(1)]), "Down": FakeAdapter(error=SourceError("HTTP 503")), "Buggy": FakeAdapter(error=KeyError("oops"))})
    results = {r.name: r for r in runner.acquire(conn, None, NOW)}
    assert results["Good"].ok and results["Good"].inserted == 1
    assert not results["Down"].ok and "503" in results["Down"].error
    assert not results["Buggy"].ok and "unexpected error" in results["Buggy"].error  # a crashing adapter is contained
    down = conn.execute("SELECT * FROM sources WHERE name = 'Down'").fetchone()
    assert down["failures"] == 1 and down["last_error"] and db.parse_ts(down["next_fetch_at"]) == NOW + timedelta(minutes=360)  # interval doubled
    assert runner.source_health(down) == "retrying"
    good = conn.execute("SELECT * FROM sources WHERE name = 'Good'").fetchone()
    assert good["failures"] == 0 and db.parse_ts(good["next_fetch_at"]) == NOW + timedelta(minutes=180) and runner.source_health(good) == "ok"


def test_only_due_active_sources_are_fetched_unless_forced(monkeypatch):
    conn = make_db()
    add_source(conn, "A")
    add_source(conn, "Paused", status="paused", url="https://p.example/feed")
    add_source(conn, "Candidate", status="candidate", url="https://c.example/feed")
    fakes = {name: FakeAdapter() for name in ("A", "Paused", "Candidate")}
    use_adapters(monkeypatch, fakes)
    assert [r.name for r in runner.acquire(conn, None, NOW)] == ["A"]
    assert runner.acquire(conn, None, NOW + timedelta(minutes=10)) == []  # not due yet
    assert [r.name for r in runner.acquire(conn, None, NOW + timedelta(minutes=10), force=True)] == ["A"]
    assert len(fakes["Paused"].windows) == 0 and len(fakes["Candidate"].windows) == 0  # discovery never implies monitoring


def test_the_window_starts_before_the_cursor_and_a_source_that_was_down_first_still_catches_up(monkeypatch):
    conn = make_db()
    add_source(conn, "A")
    fake = FakeAdapter(error=SourceError("down"))
    use_adapters(monkeypatch, {"A": fake})
    runner.acquire(conn, None, NOW)  # first attempt fails
    fake.error = None
    later = NOW + timedelta(days=5)
    runner.acquire(conn, None, later, force=True)
    assert fake.windows[0][0] == NOW - timedelta(days=21)  # first run: the feed's first-look-back
    assert fake.windows[1][0] == NOW - timedelta(days=21)  # still anchored to the FIRST ATTEMPT, not to "now"
    runner.acquire(conn, None, later + timedelta(days=1), force=True)
    assert fake.windows[2][0] == later - runner.OVERLAP  # after a success: a little before the cursor
    runner.acquire(conn, None, later + timedelta(days=200), force=True)
    assert fake.windows[3][0] == later + timedelta(days=200) - runner.MAX_LOOKBACK  # never further back than the cap


def test_repeated_failures_mark_a_source_unhealthy_and_cap_the_backoff(monkeypatch):
    conn = make_db()
    add_source(conn, "A")
    use_adapters(monkeypatch, {"A": FakeAdapter(error=SourceError("down"))})
    moment = NOW
    for _ in range(8):
        runner.acquire(conn, None, moment, force=True)
        moment += timedelta(hours=1)
    row = conn.execute("SELECT * FROM sources WHERE name = 'A'").fetchone()
    assert row["failures"] == 8 and runner.source_health(row) == "unhealthy" and row["status"] == "active"  # never removed automatically
    assert db.parse_ts(row["next_fetch_at"]) - (moment - timedelta(hours=1)) == runner.MAX_BACKOFF


def test_a_failure_that_never_reached_the_source_is_retried_soon_not_backed_off_for_hours(monkeypatch):
    """Found on the first real run: a DNS hiccup on the laptop put five healthy sources on a multi-hour backoff."""
    conn = make_db()
    add_source(conn, "A", interval=720)
    use_adapters(monkeypatch, {"A": FakeAdapter(error=SourceError("https://a.example/feed: network error: [Errno 11001] getaddrinfo failed (gave up after 4 attempts)"))})
    for _ in range(4):
        runner.acquire(conn, None, NOW, force=True)
    row = conn.execute("SELECT * FROM sources WHERE name = 'A'").fetchone()
    assert db.parse_ts(row["next_fetch_at"]) == NOW + runner.NETWORK_RETRY and row["failures"] == 4 and runner.source_health(row) == "retrying"


def test_an_adapters_memory_is_saved_only_after_a_successful_fetch(monkeypatch):
    conn = make_db()
    add_source(conn, "Page", "page")
    fake = FakeAdapter([raw(1)], state={"seen": ["https://example.org/1"]})
    use_adapters(monkeypatch, {"Page": fake})
    runner.acquire(conn, None, NOW)
    assert json.loads(conn.execute("SELECT state FROM sources").fetchone()[0]) == {"seen": ["https://example.org/1"]}


# ---------- adapters ----------


@pytest.mark.parametrize("period, expected", [("Nov 10 - Dec 15, 2026", "2026-12-15"), ("Dec 01 - 15, 2026", "2026-12-15"), ("Jan 05 - Feb 02, 2027", "2027-02-02"), ("", None), ("soon", None)])
def test_a_hackathons_deadline_is_the_end_of_its_submission_period(period, expected):
    assert devpost_deadline(period, NOW) == expected


def test_devpost_and_codeforces_listings_become_opportunities_with_api_supplied_deadlines():
    devpost = json.dumps({"hackathons": [{"id": 7, "title": "Agents Hack", "url": "https://agents.devpost.com/", "submission_period_dates": "Nov 10 - Dec 15, 2026",
                                          "displayed_location": {"location": "Online"}, "organization_name": "Acme", "prize_amount": "$<span>5,000</span>", "themes": [{"name": "AI"}]}], "meta": {}})
    (hack,) = parse_devpost(devpost, "Devpost", NOW)
    assert hack.kind == "opportunity" and hack.signals["deadline"] == "2026-12-15" and "5,000" in hack.content and hack.signals["organisation"] == "Acme"
    contests = json.dumps({"status": "OK", "result": [{"id": 1, "name": "Round A", "phase": "BEFORE", "startTimeSeconds": 1795000000, "durationSeconds": 7200, "type": "CF"},
                                                      {"id": 2, "name": "Finished", "phase": "FINISHED", "startTimeSeconds": 1700000000}]})
    (contest,) = parse_codeforces(contests, "Codeforces", NOW)
    assert contest.title == "Round A" and contest.signals["deadline"] == "2026-11-18" and contest.url.endswith("/contests/1")
    with pytest.raises(ValueError):
        parse_codeforces(json.dumps({"status": "FAILED"}), "Codeforces", NOW)


def test_a_job_board_keeps_only_early_career_postings(monkeypatch):
    jobs = {"jobs": [{"id": 1, "title": "Software Engineering Intern, Summer 2027", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "location": {"name": "Bengaluru"}, "content": "&lt;p&gt;Build things.&lt;/p&gt;"},
                     {"id": 2, "title": "Staff Engineer, Payments", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2", "location": {"name": "London"}, "content": "x"}]}
    assert len(parse_greenhouse(json.dumps(jobs), "Acme", "Acme")) == 2
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=jobs)))
    source = adapters.build_adapter({"name": "Acme jobs", "kind": "json_api", "url": "", "config": json.dumps({"preset": "greenhouse", "board": "acme", "organisation": "Acme"}), "state": "{}"})
    (posting,) = source.fetch(client, NOW, NOW)
    assert posting.title.startswith("Software Engineering Intern") and posting.kind == "opportunity" and posting.content == "Build things."
    assert posting.signals == {"location": "Bengaluru", "organisation": "Acme", "opportunity_type": "internship"}
    bad = adapters.build_adapter({"name": "x", "kind": "json_api", "url": "", "config": json.dumps({"preset": "greenhouse", "board": "../../etc"}), "state": "{}"})
    with pytest.raises(SourceError, match="invalid job-board id"):
        bad.fetch(client, NOW, NOW)


@pytest.mark.parametrize("url, expected", [("https://boards.greenhouse.io/acme/jobs/1", ("greenhouse", "acme")), ("jobs.lever.co/widgets", ("lever", "widgets")),
                                           ("https://jobs.ashbyhq.com/Foo.Bar", ("ashby", "Foo.Bar")), ("https://example.com/careers", None), ("https://boards.greenhouse.io/", None)])
def test_known_job_boards_are_recognised_from_their_address(url, expected):
    assert detect_job_board(url) == expected


@pytest.mark.parametrize("title, early_career", [
    ("Software Engineering Intern, Summer 2027", True), ("Research Fellowship", True), ("AI Residency Programme", True), ("New Grad Software Engineer", True),
    ("University Graduate, Quantitative Research", True), ("Student Researcher", True), ("Co-op, Hardware", True), ("Research Assistant", True),
    # found by running against real boards: fragments must not match ordinary senior roles
    ("Administrative Business Partner, Office of the President", False), ("AI Solutions Program Manager, Finance", False),
    ("Senior Technical Program Manager - Security", False), ("Staff Engineer, Internal Tools", False), ("Head of Residential Sales", False),
    ("Fellowship of the Ring Tour Guide", True),  # a title filter cannot know everything; extraction and the owner decide next
])
def test_the_job_title_filter_matches_whole_words_only(title, early_career):
    import re

    assert bool(re.search(adapters.JOB_TITLE_PATTERN, title, re.I)) is early_career


@pytest.mark.parametrize("text, matches", [("Summer Research Program 2027", True), ("Apply to the Student Fellowship", True), ("Call for applications", True),
                                           ("Program Manager", False), ("Our President's letter", False), ("Privacy policy", False)])
def test_the_page_link_filter_is_broader_but_still_whole_words(text, matches):
    import re

    assert bool(re.search(adapters.OPPORTUNITY_PATTERN, text, re.I)) is matches


def test_unknown_kinds_and_bad_config_are_reported_as_such():
    with pytest.raises(adapters.UnknownSourceKind):
        adapters.build_adapter({"name": "x", "kind": "telepathy", "url": "", "config": "{}", "state": "{}"})
    with pytest.raises(adapters.UnknownSourceKind, match="bad config"):
        adapters.build_adapter({"name": "x", "kind": "hn", "url": "", "config": json.dumps({"no_such_option": 1}), "state": "{}"})


def page_client(pages):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=pages[str(request.url)], headers={"content-type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_page_watcher_reports_only_new_matching_links(monkeypatch):
    real = adapters.guarded_get
    monkeypatch.setattr(adapters, "guarded_get", lambda client, url, **kw: real(client, url, resolver=PUBLIC))
    html_v1 = '<html><body><nav><a href="/about">About the company here</a></nav><main><a href="/careers/intern-2027">Summer Internship 2027</a><a href="/careers/staff">Staff Accountant</a></main></body></html>'
    html_v2 = html_v1.replace("</main>", '<a href="/careers/fellowship">Research Fellowship for undergraduates</a></main>')
    source = PageSource(name="Acme careers", url="https://acme.example/careers", organisation="Acme")
    first = source.fetch(page_client({"https://acme.example/careers": html_v1}), NOW, NOW)
    assert [i.title for i in first] == ["Summer Internship 2027"] and first[0].kind == "opportunity" and first[0].signals["organisation"] == "Acme"
    assert source.fetch(page_client({"https://acme.example/careers": html_v1}), NOW, NOW) == []  # nothing new
    second = source.fetch(page_client({"https://acme.example/careers": html_v2}), NOW, NOW)
    assert [i.url for i in second] == ["https://acme.example/careers/fellowship"]
    with pytest.raises(SourceError, match="no readable links"):
        source.fetch(page_client({"https://acme.example/careers": "<html><body><div id='root'></div><script>render()</script></body></html>"}), NOW, NOW)


# ---------- the guarded fetcher ----------


@pytest.mark.parametrize("address, public", [("93.184.216.34", True), ("127.0.0.1", False), ("10.1.2.3", False), ("192.168.0.5", False), ("169.254.169.254", False),
                                             ("::1", False), ("fe80::1", False), ("::ffff:10.0.0.1", False), ("2606:2800:220:1:248:1893:25c8:1946", True), ("not-an-ip", False), ("0.0.0.0", False)])
def test_only_public_addresses_count_as_public(address, public):
    assert is_public_address(address) is public


@pytest.mark.parametrize("url", ["ftp://example.org/x", "file:///etc/passwd", "javascript:alert(1)", "https://user:pw@example.org/", "https:///nohost", "http://"])
def test_urls_that_are_not_plain_http_are_refused(url):
    with pytest.raises(FetchRefused):
        check_url(url, PUBLIC)


def test_a_host_is_refused_if_any_of_its_addresses_is_private():
    with pytest.raises(FetchRefused, match="public address"):
        check_url("https://rebind.example/", lambda host: ["93.184.216.34", "10.0.0.7"])
    with pytest.raises(FetchRefused):
        check_url("http://169.254.169.254/latest/meta-data/", lambda host: [host])  # the cloud metadata address, as a literal
    with pytest.raises(FetchFailed):
        check_url("https://nxdomain.example/", lambda host: (_ for _ in ()).throw(OSError("no such host")))


def test_every_redirect_hop_is_checked_and_redirects_are_capped():
    def handler(request):
        if request.url.host == "start.example":
            return httpx.Response(302, headers={"location": "http://internal.example/admin"})
        if request.url.host == "loop.example":
            return httpx.Response(302, headers={"location": f"https://loop.example/{int(request.url.path.strip('/') or 0) + 1}"})
        return httpx.Response(200, text="secret", headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = lambda host: ["10.0.0.9"] if host == "internal.example" else ["93.184.216.34"]  # noqa: E731
    with pytest.raises(FetchRefused, match="internal.example"):
        guarded_get(client, "https://start.example/", resolver=resolver)
    with pytest.raises(FetchFailed, match="too many redirects"):
        guarded_get(client, "https://loop.example/0", resolver=resolver)


def test_bodies_are_capped_binary_is_refused_and_errors_are_reported():
    def handler(request):
        if request.url.path == "/big":
            return httpx.Response(200, text="x" * 50_000, headers={"content-type": "text/html; charset=utf-8"})
        if request.url.path == "/pdf":
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    big = guarded_get(client, "https://example.org/big", resolver=PUBLIC, max_bytes=1000)
    assert len(big.text) == 1000 and big.truncated
    with pytest.raises(FetchRefused, match="not a text document"):
        guarded_get(client, "https://example.org/pdf", resolver=PUBLIC)
    with pytest.raises(FetchFailed, match="HTTP 404"):
        guarded_get(client, "https://example.org/missing", resolver=PUBLIC)


def test_robots_txt_is_honoured_and_a_missing_one_means_allowed():
    def handler(request):
        if request.url.host == "strict.example" and request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/", headers={"content-type": "text/plain"})
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="<p>ok</p>", headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    robots = RobotsCache()
    assert guarded_get(client, "https://strict.example/public/a", resolver=PUBLIC, robots=robots).status == 200
    with pytest.raises(FetchRefused, match="robots.txt"):
        guarded_get(client, "https://strict.example/private/a", resolver=PUBLIC, robots=robots)
    assert guarded_get(client, "https://open.example/private/a", resolver=PUBLIC, robots=robots).status == 200


# ---------- text extraction and feeds ----------


def test_extraction_prefers_the_article_and_drops_chrome_and_scripts():
    page = extract("""<html><head><title> A  Title </title><meta name="description" content="Desc">
      <link rel="alternate" type="application/rss+xml" href="/feed.xml"></head><body>
      <nav><a href="/x">Navigation link text</a></nav><script>var secret = "do not include this script text anywhere";</script>
      <article><p>The first paragraph of the article is long enough to be kept by the extractor.</p>
      <p>The second paragraph is also long enough to be kept, and follows the first one.</p></article>
      <footer><p>Copyright notice text that is long enough to count as a paragraph on its own.</p></footer></body></html>""", "https://site.example/post")
    assert page.title == "A Title" and page.description == "Desc" and page.feeds == ["https://site.example/feed.xml"]
    assert page.text.count("\n\n") == 1 and "first paragraph" in page.text and "Copyright" not in page.text and "secret" not in page.text
    assert ("https://site.example/x", "Navigation link text") in page.links
    assert extract("<p>unclosed <b>markup <i>everywhere", "").text == ""  # short and malformed: no crash


def test_feed_entries_take_the_longest_text_and_the_preview_measures_the_feed():
    items = parse_entries(FEED, "Lab")
    assert [i.title for i in items] == ["A new LLM agent benchmark", "Number theory seminar notes", "Old news"]
    assert items[0].content.startswith("The full text is much longer") and items[2].content is None
    preview = preview_text(FEED, "https://lab.example/feed.xml", NOW)
    assert preview.title == "Example Lab" and preview.entries == 3 and (NOW - preview.newest).days == 1
    assert preview.text_share == 0.67 and preview.posts_per_week > 0


def test_a_feed_is_found_from_a_site_address_by_link_tag_or_conventional_path():
    def handler(request):
        url = str(request.url).rstrip("/") + ("/" if request.url.path == "/" else "")
        if url == "https://tagged.example/":
            return httpx.Response(200, text='<html><head><link rel="alternate" type="application/atom+xml" href="/custom/atom"></head><body></body></html>', headers={"content-type": "text/html"})
        if url in ("https://tagged.example/custom/atom", "https://plain.example/rss.xml", "https://direct.example/feed.xml"):
            return httpx.Response(200, text=FEED, headers={"content-type": "application/rss+xml"})
        if url in ("https://plain.example/", "https://nofeed.example/"):
            return httpx.Response(200, text="<html><body><p>hello</p></body></html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert find_feed(client, "tagged.example", NOW, resolver=PUBLIC).url == "https://tagged.example/custom/atom"
    assert find_feed(client, "https://plain.example/", NOW, resolver=PUBLIC).url == "https://plain.example/rss.xml"
    assert find_feed(client, "https://direct.example/feed.xml", NOW, resolver=PUBLIC).entries == 3
    with pytest.raises(SourceError, match="no RSS or Atom feed"):
        find_feed(client, "https://nofeed.example/", NOW, resolver=PUBLIC)
    with pytest.raises(SourceError):
        find_feed(client, "http://10.0.0.1/", NOW, resolver=lambda host: [host])


# ---------- discovery and management ----------


def test_a_discovered_feed_is_measured_and_stored_as_a_candidate_never_followed_automatically():
    conn = make_db()
    conn.execute("UPDATE topics SET level = 3 WHERE name = 'Large language models'")
    add_item(conn, "Already here", url="https://lab.example/b")
    preview = preview_text(FEED, "https://lab.example/feed.xml", NOW)
    source_id = discovery.propose(conn, preview, NOW, reason="3 well-ranked items came from this site.", lexicon=None)
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    evaluation = json.loads(row["evaluation"])
    assert (row["status"], row["origin"], row["name"]) == ("candidate", "discovered", "Example Lab")
    assert evaluation["matched_topics"] == ["Large language models"] and evaluation["freshness_days"] == 1 and evaluation["entries"] == 3
    assert evaluation["novelty"] == round(1 - 1 / 3, 3) and any("already reached you" in note for note in evaluation["notes"])
    assert discovery.propose(conn, preview, NOW, reason="again") is None  # known already
    assert runner.due_sources(conn, NOW) == [] or all(r["name"] != "Example Lab" for r in runner.due_sources(conn, NOW))


def test_sites_that_ranked_items_keep_pointing_to_are_found_and_platforms_are_ignored():
    conn = make_db()
    hn = add_source(conn, "Hacker News", "hn", general=True, domains=("software",), url=None)
    for n in range(3):
        add_item(conn, f"LLM post {n}", source=hn, url=f"https://goodblog.example/p{n}", signals={"points": 500})
        add_item(conn, f"Repo {n}", source=hn, url=f"https://github.com/o/r{n}", signals={"points": 500})
    add_item(conn, "One-off", source=hn, url="https://rare.example/x", signals={"points": 500})
    conn.execute("UPDATE items SET score = 0.9")
    conn.commit()
    assert [(host, count) for host, count, _ in discovery.linked_hosts(conn, NOW)] == [("goodblog.example", 3)]


def test_adding_approving_pausing_and_removing_a_source():
    conn = make_db()
    found = {"kind": "rss", "url": "https://lab.example/feed.xml", "homepage": "https://lab.example", "title": "Example Lab", "evaluation": {"domains": ["ai"], "score": 0.6}}
    source_id = manage.add_source(conn, found, NOW)
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    assert (row["status"], row["origin"], json.loads(row["domains"])) == ("active", "user", ["ai"])
    assert manage.add_source(conn, found, NOW) == source_id  # adding it twice is the same source
    item_id = add_item(conn, "From the lab", source=row)
    assert manage.set_status(conn, source_id, "paused", NOW) and manage.list_sources(conn, NOW)[0]["health"] == "paused"
    with pytest.raises(ValueError):
        manage.set_status(conn, source_id, "enabled", NOW)
    assert manage.update_source(conn, source_id, interval=5, domains=["mathematics", "nonsense"])
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    assert row["interval_minutes"] == 15 and json.loads(row["domains"]) == ["mathematics"]
    assert manage.delete_source(conn, source_id)
    assert item(conn, item_id)["source_name"] == "Example Lab" and item(conn, item_id)["source_id"] is None  # items outlive their source


def test_an_organisation_owns_its_watched_pages_and_removing_it_keeps_what_was_found():
    conn = make_db()
    org_id = manage.add_organisation(conn, "Acme Research", NOW)
    assert manage.add_organisation(conn, "acme research", NOW) == org_id
    source_id = manage.add_source(conn, {"kind": "json_api", "preset": "greenhouse", "board": "acme", "url": "https://boards.greenhouse.io/acme", "title": "acme"}, NOW, org_id=org_id, purpose="opportunities")
    config = json.loads(conn.execute("SELECT config FROM sources WHERE id = ?", (source_id,)).fetchone()[0])
    assert config == {"preset": "greenhouse", "board": "acme", "organisation": "Acme Research"}
    source = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    found = add_item(conn, "Research Intern", source=source, kind="opportunity")
    assert manage.list_organisations(conn)[0]["sources"][0]["id"] == source_id
    assert manage.delete_organisation(conn, org_id)
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0 and item(conn, found) is not None
