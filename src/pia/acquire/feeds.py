"""RSS/Atom for the platform: richer parsing than v1's, feed auto-discovery, and a measured preview.

`preview` is what source evaluation is built on: it reports facts about a feed (how often it posts, whether it
carries text, how recent it is) so that a recommendation never rests on a model's opinion alone.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlsplit

import httpx

from pia.acquire.safefetch import FetchFailed, FetchRefused, guarded_get
from pia.acquire.textextract import extract
from pia.http import SourceError, get_with_retry
from pia.models import RawItem
from pia.sources.base import make_item, parse_feed, parse_or_raise, strip_html, struct_to_dt, within_window

MAX_TEXT = 6000  # characters of feed-supplied text kept per item
COMMON_FEED_PATHS = ("/feed", "/feed/", "/rss.xml", "/atom.xml", "/index.xml", "/feed.xml", "/rss", "/blog/feed", "/blog/rss.xml", "/news/rss.xml")


def _entry_text(entry) -> str:
    """The longest text the feed offers for an entry: full content when present, else the summary."""
    candidates = [entry.get("summary") or ""]
    for content in entry.get("content") or []:
        candidates.append(content.get("value") or "")
    best = max((strip_html(text) for text in candidates if text), key=len, default="")
    return best[:MAX_TEXT]


def parse_entries(text: str, source: str) -> list[RawItem]:
    items = []
    for entry in parse_feed(text).entries:
        link = entry.get("link")
        body = _entry_text(entry)
        tags = [tag.get("term") for tag in entry.get("tags") or [] if tag.get("term")][:12]
        item = make_item(
            source=source,
            external_id=entry.get("id") or link,
            url=link,
            title=strip_html(entry.get("title") or ""),
            content=body or None,
            published_at=struct_to_dt(entry.get("published_parsed") or entry.get("updated_parsed")),
            signals={"tags": tags} if tags else {},
            author=(entry.get("author") or None),
        )
        if item:
            items.append(item)
    return items


@dataclass
class FeedSource:
    name: str
    url: str
    max_items: int = 60  # a feed's whole history can be thousands of entries; keep the newest

    def fetch(self, client: httpx.Client, since: datetime, until: datetime) -> list[RawItem]:
        text = get_with_retry(client, self.url, follow_redirects=True).text
        items = parse_or_raise(self.name, lambda body: parse_entries(body, self.name), text)
        items = within_window(items, since, until)
        items.sort(key=lambda item: item.published_at or until, reverse=True)
        return items[: self.max_items]


# ---------- discovery and preview ----------


@dataclass
class FeedPreview:
    url: str
    title: str = ""
    homepage: str = ""
    entries: int = 0
    newest: datetime | None = None
    posts_per_week: float = 0.0
    avg_text_chars: int = 0
    text_share: float = 0.0  # share of entries that carry any text
    samples: list[str] = field(default_factory=list)  # a few recent titles
    items: list[RawItem] = field(default_factory=list)


def preview_text(text: str, url: str, now: datetime) -> FeedPreview:
    parsed = parse_feed(text)
    items = parse_entries(text, "preview")
    preview = FeedPreview(url=url, title=strip_html(parsed.feed.get("title") or ""), homepage=parsed.feed.get("link") or "")
    preview.entries = len(items)
    preview.items = items
    dated = sorted((item.published_at for item in items if item.published_at), reverse=True)
    if dated:
        preview.newest = dated[0]
        recent = [date for date in dated if date >= now - timedelta(days=60)]
        span_days = max(1.0, (now - min(recent)).total_seconds() / 86400) if recent else 60.0
        preview.posts_per_week = round(7 * len(recent) / span_days, 1)
    lengths = [len(item.content or "") for item in items]
    if lengths:
        preview.avg_text_chars = sum(lengths) // len(lengths)
        preview.text_share = round(sum(1 for length in lengths if length > 0) / len(lengths), 2)
    preview.samples = [item.title for item in items[:5]]
    return preview


def _looks_like_feed(text: str) -> bool:
    head = text[:2000].lower()
    return "<rss" in head or "<feed" in head or "<rdf:rdf" in head


def find_feed(client: httpx.Client, url: str, now: datetime, *, resolver=None) -> FeedPreview:
    """Given a site or feed URL, return a preview of its feed. Raises SourceError when none can be found.

    Order: the URL itself; <link rel="alternate"> tags on the page; a short list of conventional paths.
    Every request goes through the guarded fetcher because the URL comes from the user or from discovery.
    """
    if "://" not in url:
        url = "https://" + url
    try:
        first = guarded_get(client, url, resolver=resolver)
    except (FetchRefused, FetchFailed) as exc:
        raise SourceError(str(exc)) from exc
    if _looks_like_feed(first.text):
        return preview_text(first.text, first.url, now)

    page = extract(first.text, first.url)
    parts = urlsplit(first.url)
    origin = f"{parts.scheme}://{parts.netloc}"
    candidates = list(dict.fromkeys(page.feeds + [urljoin(origin, path) for path in COMMON_FEED_PATHS]))
    for candidate in candidates[:14]:
        try:
            fetched = guarded_get(client, candidate, resolver=resolver, timeout=12)
        except (FetchRefused, FetchFailed):
            continue
        if _looks_like_feed(fetched.text):
            try:
                preview = preview_text(fetched.text, fetched.url, now)
            except SourceError:
                continue
            if preview.entries:
                preview.homepage = preview.homepage or origin
                preview.title = preview.title or page.title
                return preview
    raise SourceError(f"no RSS or Atom feed found at {origin}")
