"""Test doubles shared by collect/pipeline tests."""

from datetime import datetime, timezone

from pia.models import RawItem
from pia.sources.base import within_window

T0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def make_item(source: str, n: int, published_at: datetime | None, **extra) -> RawItem:
    return RawItem(
        source=source,
        external_id=str(n),
        url=f"https://example.com/{source}/{n}",
        title=f"{source} item {n}",
        published_at=published_at,
        **extra,
    )


class FakeLLM:
    """Replays scripted responses (dicts, or exceptions to raise) and records every call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete_json(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


class SmartLLM:
    """Answers both LLM stages by inspecting the request, like a very obedient model.

    scores:        title -> (category, importance); default ("ai", 3)
    editor_take:   how many candidates the editor picks (default: as many as allowed)
    triage_ok_calls: number of triage calls that succeed before every later one fails
    """

    def __init__(self, scores=None, editor_take=None, editor_error=None, triage_error=None, triage_ok_calls=None):
        self.scores = scores or {}
        self.editor_take = editor_take
        self.editor_error = editor_error
        self.triage_error = triage_error
        self.triage_ok_calls = triage_ok_calls
        self.calls: list[dict] = []

    def complete_json(self, **kwargs) -> dict:
        import json
        import re

        self.calls.append(kwargs)
        payload = json.loads(re.search(r"<(?:items|candidates)>\n(.*)\n</", kwargs["user"], re.S).group(1))
        if kwargs["schema_name"] == "triage_results":
            n_triage_calls = sum(c["schema_name"] == "triage_results" for c in self.calls)
            if self.triage_error and (self.triage_ok_calls is None or n_triage_calls > self.triage_ok_calls):
                raise self.triage_error
            return {
                "results": [
                    {
                        "index": item["index"],
                        "category": self.scores.get(item["title"], ("ai", 3))[0],
                        "importance": self.scores.get(item["title"], ("ai", 3))[1],
                        "summary": f"Summary of {item['title']}.",
                    }
                    for item in payload
                ]
            }
        if self.editor_error:
            raise self.editor_error
        limit = int(re.search(r"at most (\d+)", kwargs["user"]).group(1))
        take = limit if self.editor_take is None else min(self.editor_take, limit)
        return {
            "headlines": [
                {"index": item["index"], "explanation": f"Explained {item['title']}.", "why_it_matters": f"Because {item['title']}."}
                for item in payload[:take]
            ]
        }


def triaged_row(
    id_: int,
    category: str = "ai",
    importance: int = 3,
    *,
    source: str = "hn",
    signals: dict | None = None,
    published: str = "2026-09-18T00:00:00+00:00",
):
    """A row shaped like state.enriched_items() output, without needing a database."""
    import json
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """SELECT ? AS id, ? AS source, ? AS title, ? AS url, ? AS content_raw, ? AS signals,
                  ? AS published_at, ? AS discovered_at, ? AS category, ? AS importance, ? AS summary""",
        (
            id_,
            source,
            f"Title {id_}",
            f"https://example.com/{id_}",
            f"Body text {id_}",
            json.dumps(signals if signals is not None else {source: {}}),
            published,
            published,
            category,
            importance,
            f"Summary {id_}.",
        ),
    ).fetchone()


def entry(index: int, category="ai", importance=3, summary="A summary.") -> dict:
    return {"index": index, "category": category, "importance": importance, "summary": summary}


def batch_response(*entries: dict) -> dict:
    return {"results": list(entries)}


class FakeSource:
    """A source whose contents the test controls; records every window it is asked for."""

    def __init__(self, name: str, items: list[RawItem] | None = None, error: Exception | None = None):
        self.name = name
        self.items = items if items is not None else []
        self.error = error
        self.calls: list[tuple[datetime, datetime]] = []

    def fetch(self, client, since: datetime, until: datetime) -> list[RawItem]:
        self.calls.append((since, until))
        if self.error:
            raise self.error
        return within_window(self.items, since, until)
