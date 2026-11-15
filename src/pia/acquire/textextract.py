"""HTML to readable text, links and metadata, using only the standard library.

This is not a full readability engine. It is enough to give a model (and the reader view) the main prose of an
article: it prefers <article>/<main> when present, drops chrome (nav, header, footer, forms, scripts), and keeps
paragraph structure. A dependency-free extractor also means no third-party HTML parser sits on the path of
untrusted pages.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

_SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "button", "iframe", "template", "select", "dialog"}
_BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "pre", "blockquote", "td", "th", "dd", "dt", "figcaption", "div", "section", "br", "tr"}
_VOID = {"br", "img", "meta", "link", "input", "hr", "source", "area", "base", "col", "embed", "track", "wbr"}
_FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json")


@dataclass
class Page:
    title: str = ""
    description: str = ""
    text: str = ""  # main prose, paragraphs separated by blank lines
    links: list[tuple[str, str]] = field(default_factory=list)  # (absolute url, anchor text)
    feeds: list[str] = field(default_factory=list)  # <link rel="alternate"> feed URLs


class _Parser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.page = Page()
        self._skip_depth = 0
        self._stack: list[str] = []
        self._in_title = False
        self._focus_depth = 0  # > 0 while inside <article> or <main>
        self._all: list[str] = []
        self._focus: list[str] = []
        self._buffer: list[str] = []
        self._link_href: str | None = None
        self._link_text: list[str] = []

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        self._buffer = []
        if text:
            self._all.append(text)
            if self._focus_depth:
                self._focus.append(text)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta":
            name = (attributes.get("name") or attributes.get("property") or "").lower()
            if name in ("description", "og:description") and not self.page.description:
                self.page.description = (attributes.get("content") or "").strip()
            return
        if tag == "link":
            rel = (attributes.get("rel") or "").lower()
            if "alternate" in rel and (attributes.get("type") or "").lower() in _FEED_TYPES and attributes.get("href"):
                self.page.feeds.append(urljoin(self.base, attributes["href"]))
            return
        if tag in _VOID:
            if tag == "br":
                self._flush()
            return
        self._stack.append(tag)
        if tag in _SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in ("article", "main"):
            self._focus_depth += 1
        if tag in _BLOCK:
            self._flush()
        if tag == "a" and attributes.get("href") and not self._skip_depth_for_links():
            self._link_href = urljoin(self.base, attributes["href"])
            self._link_text = []

    def _skip_depth_for_links(self) -> bool:
        # Links inside scripts/styles are never real; links inside nav/header ARE collected (page watching needs them).
        return any(tag in ("script", "style", "noscript", "svg", "template") for tag in self._stack)

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if tag == "a" and self._link_href:
            text = re.sub(r"\s+", " ", "".join(self._link_text)).strip()
            if text and self._link_href.startswith(("http://", "https://")):
                self.page.links.append((self._link_href.split("#")[0], text))
            self._link_href = None
        if tag in _BLOCK:
            self._flush()
        if tag not in self._stack:
            return
        while self._stack:  # tolerate unclosed tags: unwind to the matching one
            popped = self._stack.pop()
            if popped in _SKIP:
                self._skip_depth -= 1
            if popped in ("article", "main"):
                self._focus_depth -= 1
            if popped == "title":
                self._in_title = False
            if popped == tag:
                break

    def handle_data(self, data):
        if self._in_title:
            self.page.title += data
            return
        if self._link_href is not None:
            self._link_text.append(data)
        if not self._skip_depth:
            self._buffer.append(data)

    def finish(self) -> Page:
        self._flush()
        focused = [p for p in self._focus if len(p) > 40]
        everything = [p for p in self._all if len(p) > 40]
        # Prefer <article>/<main> when it holds a real share of the prose; otherwise use the whole page.
        chosen = focused if sum(map(len, focused)) >= 0.4 * max(1, sum(map(len, everything))) and focused else everything
        self.page.text = "\n\n".join(chosen)
        self.page.title = re.sub(r"\s+", " ", self.page.title).strip()
        return self.page


def extract(html: str, base_url: str = "") -> Page:
    parser = _Parser(base_url)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup must never break a fetch; keep what was parsed
        pass
    return parser.finish()
