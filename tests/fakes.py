"""Test doubles shared by collect/pipeline tests."""

from datetime import datetime, timezone

from pia.models import RawItem
from pia.sources.base import within_window

T0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def make_item(source: str, n: int, published_at: datetime | None) -> RawItem:
    return RawItem(
        source=source,
        external_id=str(n),
        url=f"https://example.com/{source}/{n}",
        title=f"{source} item {n}",
        published_at=published_at,
    )


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
