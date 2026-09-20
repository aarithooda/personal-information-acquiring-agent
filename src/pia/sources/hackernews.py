"""Hacker News via the Algolia search API (server-side time and points filtering)."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from pia.http import get_with_retry
from pia.models import RawItem
from pia.sources.base import make_item, opt_str, parse_or_raise

API_URL = "https://hn.algolia.com/api/v1/search_by_date"


def parse_hn(text: str) -> list[RawItem]:
    items = []
    for hit in json.loads(text)["hits"]:
        hn_url = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        created = hit.get("created_at_i")
        item = make_item(
            source="hn",
            external_id=opt_str(hit.get("objectID")),
            url=hit.get("url") or hn_url,  # Ask HN / text posts have no external URL
            title=hit.get("title"),
            published_at=datetime.fromtimestamp(created, tz=timezone.utc) if created else None,
            signals={"points": hit.get("points"), "comments": hit.get("num_comments"), "hn_url": hn_url},
        )
        if item:
            items.append(item)
    return items


@dataclass
class HackerNewsSource:
    min_points: int = 100
    max_results: int = 100
    name: str = "hn"

    def fetch(self, client: httpx.Client, since: datetime, until: datetime) -> list[RawItem]:
        filters = (
            f"created_at_i>{int(since.timestamp())},"
            f"created_at_i<={int(until.timestamp())},"
            f"points>={self.min_points}"
        )
        params = {"tags": "story", "numericFilters": filters, "hitsPerPage": self.max_results}
        return parse_or_raise(self.name, parse_hn, get_with_retry(client, API_URL, params=params).text)
