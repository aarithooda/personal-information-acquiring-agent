"""Generic RSS/Atom source. Adding a blog is one line in config/sources.toml."""

from dataclasses import dataclass
from datetime import datetime

import httpx

from pia.http import get_with_retry
from pia.models import RawItem
from pia.sources.base import (
    make_item,
    parse_feed,
    parse_or_raise,
    strip_html,
    struct_to_dt,
    within_window,
)


def parse_rss(text: str, source: str) -> list[RawItem]:
    items = []
    for entry in parse_feed(text).entries:
        link = entry.get("link")
        summary = strip_html(entry["summary"]) if entry.get("summary") else ""
        item = make_item(
            source=source,
            external_id=entry.get("id") or link,
            url=link,
            title=entry.get("title"),
            content=summary or None,
            published_at=struct_to_dt(entry.get("published_parsed") or entry.get("updated_parsed")),
        )
        if item:
            items.append(item)
    return items


@dataclass
class RssSource:
    name: str
    url: str

    def fetch(self, client: httpx.Client, since: datetime, until: datetime) -> list[RawItem]:
        text = get_with_retry(client, self.url).text
        items = parse_or_raise(self.name, lambda t: parse_rss(t, self.name), text)
        # Feeds return their whole history (OpenAI's has 1,000+ entries), so filter here.
        return within_window(items, since, until)
