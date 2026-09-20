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
