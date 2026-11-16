"""Gmail reading at scale: paging, the cursor that never passes unread mail, retries, duplicates and the filters.

A fake Gmail (httpx.MockTransport) holds a mailbox and answers list/get like the real API, with deliberately awkward
behaviour: small pages, exclusive time bounds, injected failures.
"""

import base64
import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pia.acquire import runner
from pia.http import SourceError
from pia.integrations import gmail
from platform_helpers import NOW, add_source, make_db

SINCE = NOW - timedelta(days=3)
TRUSTED = "dean@institute.example"


def encode(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class Mailbox:
    def __init__(self, page_size=100):
        self.messages = {}  # id -> (when, sender, subject, body)
        self.page_size = page_size
        self.list_calls = []
        self.get_calls = []
        self.fail_once = set()  # ids whose first read answers 503
        self.fail_always = set()  # ids whose reads always answer 500

    def add(self, message_id, when, sender=TRUSTED, subject="Summer internship: applications open", body="Apply soon."):
        self.messages[message_id] = (when, sender, subject, body)

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request):
        assert request.method == "GET", "mail must only ever be read"
        path = request.url.path
        if path.endswith("/messages"):
            q = request.url.params["q"]
            after = int(re.search(r"after:(\d+)", q).group(1))
            before = int(re.search(r"before:(\d+)", q).group(1))
            self.list_calls.append((after, before, request.url.params.get("pageToken")))
            # exclusive on both ends: the worst case a real boundary could behave like
            hits = sorted((m for m in self.messages.items() if after < m[1][0].timestamp() < before), key=lambda m: m[1][0], reverse=True)
            start = int(request.url.params.get("pageToken") or 0)
            page = hits[start:start + self.page_size]
            body = {"messages": [{"id": key} for key, _ in page]}
            if start + self.page_size < len(hits):
                body["nextPageToken"] = str(start + self.page_size)
            return httpx.Response(200, json=body)
        message_id = path.rsplit("/", 1)[1]
        self.get_calls.append(message_id)
        if message_id in self.fail_always:
            return httpx.Response(500)
        if message_id in self.fail_once:
            self.fail_once.discard(message_id)
            return httpx.Response(503)
        when, sender, subject, body = self.messages[message_id]
        return httpx.Response(200, json={"id": message_id, "threadId": "t" + message_id, "internalDate": str(int(when.timestamp() * 1000)), "payload": {
            "headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": f"Someone <{sender}>"},
                        {"name": "Date", "value": when.strftime("%a, %d %b %Y %H:%M:%S +0000")}],
            "mimeType": "text/plain", "body": {"data": encode(body)}}})


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    from pia.http import request_with_retry

    monkeypatch.setitem(request_with_retry.__kwdefaults__, "sleep", lambda seconds: None)  # retries must not slow the tests


@pytest.fixture
def token(tmp_path):
    path = tmp_path / "gmail.json"
    gmail.save_token({"client_id": "id", "client_secret": "secret", "refresh_token": "r", "access_token": "t",
                      "expires_at": (NOW + timedelta(days=1)).isoformat()}, path)
    return str(path)


def source(token, **kwargs):
    return gmail.GmailSource(name="University mail", senders=[TRUSTED], token_file=token, **kwargs)


# ---------- the filters ----------


def test_the_query_has_no_generic_keywords_and_carries_the_window_and_exclusions():
    for generic in ("notice", "registration", "opportunity", "talk", "workshop"):
        assert generic not in gmail.DEFAULT_KEYWORDS  # a bare generic word is not a trigger
    query = gmail.build_query(SINCE, [TRUSTED], ["internship", "call for papers"], until=NOW, exclude_subjects=["viva"], exclude_senders=["spam@x.example"])
    assert f"after:{int(SINCE.timestamp())}" in query and f"before:{int(NOW.timestamp())}" in query
    assert "-subject:viva" in query and "-from:spam@x.example" in query and f"from:({TRUSTED})" in query and '"call for papers"' in query


def test_decide_trusted_senders_keywords_and_exclusions():
    rules = dict(senders=[TRUSTED, "@trusted-domain.example"], keywords=gmail.DEFAULT_KEYWORDS,
                 exclude_subjects=gmail.DEFAULT_EXCLUDE_SUBJECTS, exclude_senders=gmail.DEFAULT_EXCLUDE_SENDERS)
    assert gmail.decide(TRUSTED, "Timetable for the end-term exams", "", **rules) == (True, "trusted sender")
    assert gmail.decide("DEAN@Institute.Example", "Anything", "", **rules)[0]  # addresses compare case-insensitively
    assert gmail.decide("x@trusted-domain.example", "Hello", "", **rules)[0]
    assert gmail.decide("club@institute.example", "Hackathon this weekend", "", **rules) == (True, "keyword: hackathon")
    assert gmail.decide("club@institute.example", "Trek to Har Ki Dun", "Register now. Workshop on weekend. Notice.", **rules)[0] is False
    assert gmail.decide(TRUSTED, "Notice for Ph.D. Viva-Voce Examination of Mr X", "internship", **rules)[0] is False  # exclusion beats trust
    assert gmail.decide("club@institute.example", "Revival of the old library", "internship", **rules)[0]  # "viva" is a whole word only
    assert gmail.decide("noreply-emailsupport@iitr.ac.in", "Updates: Your List of Quarantined Emails", "", **rules)[0] is False
    assert gmail.decide("someone@else.example", "Free pizza", "", **rules)[0] is False


# ---------- paging, the cursor, retries and duplicates ----------


def test_more_than_forty_messages_in_one_day_are_all_read_across_pages(token):
    box = Mailbox(page_size=100)
    for i in range(250):
        box.add(f"m{i}", SINCE + timedelta(minutes=5 + i))
    found = source(token).fetch(httpx.Client(transport=box.transport()), SINCE, SINCE + timedelta(days=1))
    assert len(found) == 250 and len({item.url for item in found}) == 250
    assert [call[2] for call in box.list_calls] == [None, "100", "200"]  # every page was followed
    assert len(box.get_calls) == 250


def test_a_run_stops_only_on_a_day_boundary_and_the_next_run_continues_with_nothing_lost_or_repeated(token):
    box = Mailbox(page_size=7)
    for i in range(150):
        box.add(f"m{i}", SINCE + timedelta(hours=1, minutes=i * 28))  # about 3 days of mail
    adapter = source(token, max_messages=60)
    client = httpx.Client(transport=box.transport())
    collected, position, runs = [], SINCE, 0
    while True:
        runs += 1
        got = adapter.fetch(client, position, NOW)
        collected += got
        reached = datetime.fromisoformat(adapter.state["fetched_until"])
        stored_before_cursor = [k for k, m in box.messages.items() if position <= m[0] < reached]
        assert {item.url.split("pia_message=")[1].split("#")[0] for item in got} >= set(stored_before_cursor)  # nothing before the cursor was skipped
        if not adapter.state["more"]:
            break
        assert (reached - SINCE) % timedelta(days=1) == timedelta(0) and reached < NOW  # stopped on a day boundary
        position = reached
        assert runs < 10
    assert runs >= 2
    urls = [item.url for item in collected]
    assert len(set(urls)) == 150  # every message arrived
    assert len(urls) - len(set(urls)) <= 3  # at most the boundary re-reads, which the store de-duplicates by URL


def test_a_message_dated_exactly_on_a_day_boundary_is_neither_missed_nor_read_twice(token):
    box = Mailbox()
    box.add("edge", SINCE + timedelta(days=1))  # the fake treats both bounds as exclusive
    found = source(token).fetch(httpx.Client(transport=box.transport()), SINCE, SINCE + timedelta(days=2))
    assert [item.url for item in found] and len(found) == 1 and box.get_calls == ["edge"]


def test_a_transient_failure_is_retried_and_does_not_lose_the_message(token):
    box = Mailbox()
    box.add("flaky", SINCE + timedelta(hours=2))
    box.add("fine", SINCE + timedelta(hours=3))
    box.fail_once.add("flaky")
    found = source(token).fetch(httpx.Client(transport=box.transport()), SINCE, SINCE + timedelta(days=1))
    assert len(found) == 2 and box.get_calls.count("flaky") == 2


def test_a_failure_part_way_keeps_finished_days_and_leaves_the_cursor_before_the_broken_day(token):
    box = Mailbox()
    box.add("day1", SINCE + timedelta(hours=2))
    box.add("day2", SINCE + timedelta(days=1, hours=2))
    box.add("day3-a", SINCE + timedelta(days=2, hours=1))
    box.add("day3-broken", SINCE + timedelta(days=2, hours=2))
    box.fail_always.add("day3-broken")
    adapter = source(token)
    found = adapter.fetch(httpx.Client(transport=box.transport()), SINCE, SINCE + timedelta(days=3))
    assert len(found) == 2  # day 3 is abandoned as a whole, even its readable message
    assert datetime.fromisoformat(adapter.state["fetched_until"]) == SINCE + timedelta(days=2)
    assert adapter.state["more"] is False  # a broken day is retried at the normal interval, not at once

    # with the very first day broken nothing is finished, so it reports the failure and the cursor cannot move
    box.fail_always.update({"day1"})
    with pytest.raises(SourceError):
        source(token).fetch(httpx.Client(transport=box.transport()), SINCE, SINCE + timedelta(days=3))


def test_a_message_deleted_between_listing_and_reading_is_skipped_not_fatal(token):
    box = Mailbox()
    box.add("gone", SINCE + timedelta(hours=1))
    box.add("here", SINCE + timedelta(hours=2))
    original = box.handle

    def handler(request):
        if request.url.path.endswith("/gone"):
            return httpx.Response(404)
        return original(request)

    found = source(token).fetch(httpx.Client(transport=httpx.MockTransport(handler)), SINCE, SINCE + timedelta(days=1))
    assert len(found) == 1


def test_filters_are_enforced_on_what_gmail_returns(token):
    box = Mailbox()
    box.add("trusted", SINCE + timedelta(hours=1), subject="Timetable", body="Exams.")
    box.add("keyword", SINCE + timedelta(hours=2), sender="club@institute.example", subject="Hackathon on Saturday")
    box.add("viva", SINCE + timedelta(hours=3), subject="Open Notice for Ph.D. Viva-Voce Examination")
    box.add("noise", SINCE + timedelta(hours=4), sender="club@institute.example", subject="Trek", body="Workshop notice registration")
    adapter = source(token)
    found = adapter.fetch(httpx.Client(transport=box.transport()), SINCE, SINCE + timedelta(days=1))
    assert sorted(item.title for item in found) == ["Hackathon on Saturday", "Timetable"] and adapter.dropped == 2


# ---------- through the real runner and database ----------


def test_the_runner_stores_more_than_forty_and_moves_the_cursor_only_over_finished_days(token):
    conn = make_db()
    box = Mailbox(page_size=25)
    for i in range(130):
        box.add(f"m{i}", NOW - timedelta(days=4) + timedelta(minutes=i * 40))  # about 3.6 days of mail
    add_source(conn, "University mail", "gmail", domains=("university",), config={"senders": [TRUSTED], "token_file": token, "max_messages": 50}, interval=60)
    client = httpx.Client(transport=box.transport())
    moment, runs = NOW, 0
    while True:
        runs += 1
        (result,) = runner.acquire(conn, client, moment, force=True)
        assert result.ok
        cursor = conn.execute("SELECT MAX(fetched_until) FROM fetch_runs WHERE ok = 1").fetchone()[0]
        stored_urls = {row[0] for row in conn.execute("SELECT url FROM items")}
        due = conn.execute("SELECT next_fetch_at FROM sources").fetchone()[0]
        if datetime.fromisoformat(cursor) >= moment:
            break
        assert datetime.fromisoformat(due) - moment == timedelta(minutes=1)  # asks to be called again at once
        # everything older than the cursor (less the one-second boundary overlap) is already stored
        older = [k for k, m in box.messages.items() if m[0] < datetime.fromisoformat(cursor) - timedelta(seconds=2)]
        assert all(any(f"pia_message={k}#" in url for url in stored_urls) for k in older)
        moment += timedelta(minutes=1)
        assert runs < 12
    assert runs >= 2 and conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 130

    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    runner.acquire(conn, client, moment + timedelta(hours=2), force=True)  # a repeat run adds nothing
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == count


def test_a_message_that_keeps_failing_holds_the_cursor_before_its_day_and_is_not_retried_every_minute(token):
    conn = make_db()
    box = Mailbox()
    broken_at = NOW - timedelta(days=1, hours=3)
    box.add("ok-before", NOW - timedelta(days=5))
    box.add("broken", broken_at)
    box.fail_always.add("broken")
    add_source(conn, "University mail", "gmail", domains=("university",), config={"senders": [TRUSTED], "token_file": token}, interval=60)
    client = httpx.Client(transport=box.transport())
    (result,) = runner.acquire(conn, client, NOW, force=True)
    assert result.ok and conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1  # the message before the broken day was kept
    cursor = datetime.fromisoformat(conn.execute("SELECT MAX(fetched_until) FROM fetch_runs WHERE ok = 1").fetchone()[0])
    assert cursor <= broken_at  # the cursor did not pass the unreadable message
    assert datetime.fromisoformat(conn.execute("SELECT next_fetch_at FROM sources").fetchone()[0]) - NOW == timedelta(minutes=60)

    box.fail_always.clear()  # the message becomes readable: it is picked up, nothing was lost
    runner.acquire(conn, client, NOW + timedelta(hours=1), force=True)
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2


def test_a_broken_message_in_the_newest_day_leaves_the_cursor_before_it(token):
    conn = make_db()
    box = Mailbox()
    box.add("m1", NOW - timedelta(hours=2))
    box.fail_always.add("m1")
    add_source(conn, "University mail", "gmail", domains=("university",), config={"senders": [TRUSTED], "token_file": token}, interval=60)
    (result,) = runner.acquire(conn, httpx.Client(transport=box.transport()), NOW, force=True)
    # every earlier day is empty and completes; only the last day (the broken one) is held back
    cursor = datetime.fromisoformat(conn.execute("SELECT MAX(fetched_until) FROM fetch_runs WHERE ok = 1").fetchone()[0])
    assert result.ok and cursor < NOW - timedelta(hours=2) and conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
