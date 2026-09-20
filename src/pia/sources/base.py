"""The adapter contract and helpers shared by every source.

An adapter has two halves on purpose:
  * a pure `parse_*(text) -> list[RawItem]` (no network, easy to test), and
  * a small `fetch(client, since, until)` that builds the request and calls parse.
The rest of the system only ever sees the `Source` protocol.
"""

import html
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from time import struct_time
from typing import Protocol, TypeVar

import feedparser
import httpx

from pia.http import SourceError
from pia.models import RawItem

log = logging.getLogger(__name__)


class Source(Protocol):
    name: str

    def fetch(self, client: httpx.Client, since: datetime, until: datetime) -> list[RawItem]:
        """Items published in [since, until]. Raises SourceError if the source is unusable."""
        ...


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_html(text: str) -> str:
    return clean_text(html.unescape(re.sub(r"<[^>]+>", " ", text)))


def opt_str(value) -> str | None:
    return None if value is None else str(value)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def struct_to_dt(value: struct_time | None) -> datetime | None:
    """feedparser hands back UTC struct_time values (or None when a date is missing)."""
    if not value:
        return None
    return datetime(*value[:6], tzinfo=timezone.utc)


def within_window(items: list[RawItem], since: datetime, until: datetime) -> list[RawItem]:
    """Client-side window filter for sources that cannot filter server-side.

    Undated items are kept: dropping them would silently lose data, and dedup
    already protects us from seeing the same item twice.
    """
    return [i for i in items if i.published_at is None or since <= i.published_at <= until]


def make_item(**fields) -> RawItem | None:
    """Build a RawItem, or log and return None if the entry is unusable (missing title etc.)."""
    try:
        return RawItem(**fields)
    except ValueError as exc:
        log.warning("skipping malformed %s entry: %s", fields.get("source"), exc)
        return None


def parse_feed(text: str):
    """Parse RSS/Atom. A feed with zero entries is valid; something that isn't a feed is not."""
    parsed = feedparser.parse(text)
    if parsed.bozo and not parsed.entries:
        raise SourceError(f"response is not a valid feed: {parsed.get('bozo_exception')}")
    return parsed


T = TypeVar("T")


def parse_or_raise(name: str, parse: Callable[[str], T], text: str) -> T:
    """Turn any 'the payload was not what we expected' failure into a SourceError."""
    try:
        return parse(text)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"{name}: malformed response ({exc})") from exc
