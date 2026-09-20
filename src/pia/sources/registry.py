"""Builds Source objects from config/sources.toml."""

import tomllib
from pathlib import Path

from pia.sources.arxiv import ArxivSource
from pia.sources.base import Source
from pia.sources.github import GitHubSource
from pia.sources.hackernews import HackerNewsSource
from pia.sources.hf_papers import HFPapersSource
from pia.sources.rss import RssSource

SOURCE_TYPES: dict[str, type] = {
    "arxiv": ArxivSource,
    "hf_papers": HFPapersSource,
    "hn": HackerNewsSource,
    "github": GitHubSource,
    "rss": RssSource,
}


def load_sources(path: str | Path) -> list[Source]:
    with open(path, "rb") as f:
        entries = tomllib.load(f).get("source", [])

    sources: list[Source] = []
    seen: set[str] = set()
    for entry in entries:
        params = dict(entry)
        kind = params.pop("type", None)
        if kind not in SOURCE_TYPES:
            raise ValueError(f"unknown source type {kind!r} (known: {', '.join(SOURCE_TYPES)})")
        try:
            source = SOURCE_TYPES[kind](**params)
        except TypeError as exc:
            raise ValueError(f"bad config for source type {kind!r}: {exc}") from exc
        if source.name in seen:
            raise ValueError(f"duplicate source name {source.name!r}")
        seen.add(source.name)
        sources.append(source)
    return sources
