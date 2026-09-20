"""Hugging Face Daily Papers: community-curated arXiv papers with upvote counts.

This is our answer to arXiv's volume problem: humans have already filtered, and
`upvotes` gives us a deterministic pre-filter before any LLM is involved.
Items point at the arXiv URL, so the same paper from the arXiv adapter merges.
"""

import json
from dataclasses import dataclass
from datetime import datetime

import httpx

from pia.http import get_with_retry
from pia.models import RawItem, ensure_utc
from pia.sources.base import make_item, parse_iso, parse_or_raise

API_URL = "https://huggingface.co/api/daily_papers"


def parse_hf_papers(text: str) -> list[RawItem]:
    items = []
    for entry in json.loads(text):
        paper = entry["paper"]
        paper_id = paper.get("id")
        featured = parse_iso(paper.get("submittedOnDailyAt"))
        item = make_item(
            source="hf_papers",
            external_id=paper_id,
            url=f"https://arxiv.org/abs/{paper_id}" if paper_id else None,
            title=paper.get("title") or entry.get("title"),
            content=paper.get("summary") or entry.get("summary"),
            published_at=parse_iso(paper.get("publishedAt")),
            signals={
                "upvotes": paper.get("upvotes") or 0,
                "featured_at": ensure_utc(featured).isoformat() if featured else None,
                "hf_url": f"https://huggingface.co/papers/{paper_id}",
            },
        )
        if item:
            items.append(item)
    return items


@dataclass
class HFPapersSource:
    min_upvotes: int = 10
    max_results: int = 100
    name: str = "hf_papers"

    def fetch(self, client: httpx.Client, since: datetime, until: datetime) -> list[RawItem]:
        response = get_with_retry(client, API_URL, params={"limit": self.max_results})
        items = parse_or_raise(self.name, parse_hf_papers, response.text)
        kept = []
        for item in items:
            if item.signals["upvotes"] < self.min_upvotes:
                continue
            # "New to you" means when it was featured, not when the paper was written.
            featured = parse_iso(item.signals["featured_at"]) or item.published_at
            if featured is None or since <= featured <= until:
                kept.append(item)
        return kept
