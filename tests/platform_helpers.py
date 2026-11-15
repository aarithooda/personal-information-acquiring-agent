"""Shared fixtures for the platform tests: an in-memory database seeded with a small taxonomy, and fakes."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from pia.intel.classify import classify_new
from pia.intel.score import rescore
from pia.models import RawItem
from pia.store import db, seed

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)

TAXONOMY = {
    "domain": [
        {"slug": "ai", "name": "Artificial Intelligence", "short": "AI", "hue": 268, "arxiv": ["cs.AI", "cs.LG"],
         "keywords": ["llm", "language model", "neural network", "machine learning", "ai agent", "ai agents"],
         "topics": ["AI agents | agentic | multi-agent", "Large language models | LLM | LLMs", "Retrieval-augmented generation | RAG"]},
        {"slug": "mathematics", "name": "Mathematics", "short": "Maths", "hue": 38, "arxiv": ["math."],
         "keywords": ["theorem", "proof", "conjecture", "number theory", "mathematics"],
         "topics": ["Number theory | prime numbers | primes", "Linear algebra | matrix theory"]},
        {"slug": "software", "name": "Software", "short": "Software", "hue": 218, "arxiv": ["cs.SE"],
         "keywords": ["compiler", "database", "rust", "python", "open source"], "topics": ["Databases | database | postgres", "Rust"]},
        {"slug": "hackathons", "name": "Hackathons & Competitions", "short": "Hackathons", "hue": 308, "opportunities": True, "arxiv": [],
         "keywords": ["hackathon", "competition", "contest"], "topics": ["Hackathons | hackathon", "Crypto markets | bitcoin"]},
        {"slug": "university", "name": "University Announcements", "short": "University", "hue": 25, "opportunities": True, "arxiv": [],
         "keywords": ["academic calendar"], "topics": ["Campus opportunities | campus internship"]},
    ]
}


def make_db(path=":memory:") -> sqlite3.Connection:
    conn = db.connect(path)
    seed.seed_taxonomy(conn, TAXONOMY, NOW)
    return conn


def add_source(conn, name="Feed", kind="rss", *, domains=("ai",), general=False, status="active", purpose="news", trust=0.7,
               url="https://example.org/feed.xml", config=None, interval=180) -> sqlite3.Row:
    conn.execute(
        """INSERT INTO sources (name, kind, url, config, domains, general, status, purpose, trust, interval_minutes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, kind, url, json.dumps(config or {}), json.dumps(list(domains)), int(general), status, purpose, trust, interval, db.iso(NOW)),
    )
    conn.commit()
    return conn.execute("SELECT * FROM sources WHERE name = ?", (name,)).fetchone()


def add_item(conn, title, *, source=None, content=None, url=None, published=NOW, acquired=NOW, signals=None, kind="article", process=True) -> int:
    """Insert one item through the real upsert path, then (by default) classify and score it."""
    from pia.acquire.runner import upsert_item

    if source is None:
        source = conn.execute("SELECT * FROM sources ORDER BY id LIMIT 1").fetchone() or add_source(conn)
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    raw = RawItem(source=source["name"], external_id=f"x{count}", url=url or f"https://example.org/post/{count}", title=title, content=content,
                  published_at=published, signals=signals or {}, kind=kind if kind != "article" else None)
    upsert_item(conn, source, raw, acquired)
    conn.commit()
    item_id = conn.execute("SELECT id FROM items ORDER BY id DESC LIMIT 1").fetchone()[0]
    if process:
        classify_new(conn, lexicon())
        rescore(conn, acquired, item_ids=[item_id])
    return item_id


def lexicon():
    from pia.intel.classify import DomainRule, Lexicon, _phrase_pattern

    return Lexicon([DomainRule(d["slug"], _phrase_pattern(d.get("keywords", [])), tuple(d.get("arxiv", [])), bool(d.get("opportunities"))) for d in TAXONOMY["domain"]])


def topic_id(conn, name: str) -> int:
    return conn.execute("SELECT id FROM topics WHERE name = ?", (name,)).fetchone()[0]


def item(conn, item_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


class ScriptedLLM:
    """Replays responses (dicts, callables taking the request, or exceptions) and records every call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.last_usage = {"input_tokens": 100, "output_tokens": 50}

    def complete_json(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response(kwargs) if callable(response) else response


def assess_everything(significance=3, domain="ai", kind="article", topics=(), entities=(), summary="A plain summary."):
    """An LLM response function that assesses every item in the request the same way."""
    import re

    def respond(request: dict) -> dict:
        items = json.loads(re.search(r"<items>\n(.*)\n</items>", request["user"], re.S).group(1))
        return {"results": [{"index": entry["index"], "domain": domain, "kind": kind, "significance": significance, "topics": list(topics),
                             "entities": [{"name": name, "type": "org"} for name in entities], "summary": summary} for entry in items]}

    return respond


def days(n: float) -> timedelta:
    return timedelta(days=n)
