"""GitHub repositories via the search API.

There is no official "trending" API. We approximate it: repositories *created*
in the window that already have stars. Known limit: an old repo that suddenly
goes viral is not found this way.
"""

import json
from dataclasses import dataclass
from datetime import datetime

import httpx

from pia.http import get_with_retry
from pia.models import RawItem, ensure_utc
from pia.sources.base import make_item, opt_str, parse_iso, parse_or_raise

API_URL = "https://api.github.com/search/repositories"


def parse_github(text: str) -> list[RawItem]:
    items = []
    for repo in json.loads(text)["items"]:
        topics = repo.get("topics") or []
        content = " ".join(filter(None, [repo.get("description"), f"Topics: {', '.join(topics)}." if topics else None]))
        item = make_item(
            source="github",
            external_id=opt_str(repo.get("id")),
            url=repo.get("html_url"),
            title=repo.get("full_name"),
            content=content or None,
            published_at=parse_iso(repo.get("created_at")),
            signals={"stars": repo.get("stargazers_count"), "language": repo.get("language"), "topics": topics},
        )
        if item:
            items.append(item)
    return items


@dataclass
class GitHubSource:
    query: str = "llm OR agent OR ai"
    min_stars: int = 50
    max_results: int = 30
    name: str = "github"

    def fetch(self, client: httpx.Client, since: datetime, until: datetime) -> list[RawItem]:
        day = ensure_utc(since).strftime("%Y-%m-%d")
        params = {
            "q": f"{self.query} created:>={day} stars:>={self.min_stars}",
            "sort": "stars",
            "order": "desc",
            "per_page": self.max_results,
        }
        return parse_or_raise(self.name, parse_github, get_with_retry(client, API_URL, params=params).text)
