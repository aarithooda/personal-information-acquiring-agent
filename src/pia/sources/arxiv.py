"""arXiv via its official Atom API.

arXiv publishes hundreds of papers a day in the big ML categories and has no
popularity signal, so keep `categories` narrow and `max_results` capped.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from pia.http import get_with_retry
from pia.models import RawItem, ensure_utc
from pia.normalize import canonicalize_url
from pia.sources.base import clean_text, make_item, parse_feed, parse_or_raise, struct_to_dt

log = logging.getLogger(__name__)

API_URL = "https://export.arxiv.org/api/query"


def parse_arxiv(text: str) -> list[RawItem]:
    items = []
    for entry in parse_feed(text).entries:
        link = entry.get("link") or entry.get("id") or ""
        try:
            paper_id = canonicalize_url(entry.get("id") or link).removeprefix("https://arxiv.org/abs/")
        except ValueError:
            paper_id = None
        item = make_item(
            source="arxiv",
            external_id=paper_id,
            url=link,
            title=clean_text(entry.get("title", "")),
            content=clean_text(entry["summary"]) if entry.get("summary") else None,
            published_at=struct_to_dt(entry.get("published_parsed")),
            signals={"categories": [tag["term"] for tag in entry.get("tags", [])]},
        )
        if item:
            items.append(item)
    return items


@dataclass
class ArxivSource:
    categories: list[str]
    max_results: int = 100
    name: str = "arxiv"

    def fetch(self, client: httpx.Client, since: datetime, until: datetime) -> list[RawItem]:
        stamp = lambda dt: ensure_utc(dt).strftime("%Y%m%d%H%M")  # noqa: E731
        cats = " OR ".join(f"cat:{c}" for c in self.categories)
        params = {
            "search_query": f"({cats}) AND submittedDate:[{stamp(since)} TO {stamp(until)}]",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": self.max_results,
        }
        items = parse_or_raise(self.name, parse_arxiv, get_with_retry(client, API_URL, params=params).text)
        if len(items) >= self.max_results:
            log.warning("arxiv: hit max_results=%d; older papers in this window were not fetched", self.max_results)
        return items
