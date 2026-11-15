"""Deterministic classification: which spaces an item belongs to, what kind of thing it is, which topics it
touches. No model is involved, so every acquired item is browsable and rankable immediately and for free.

Evidence, strongest first: the source's declared spaces (a mathematics blog posts mathematics), arXiv's own
categories, then the per-space vocabulary in config/taxonomy.toml (a title hit counts more than a text hit).
A later model assessment may refine the result; it never has to exist for the item to be usable.
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field

from pia.store.seed import TAXONOMY_FILE, load_toml

TEXT_WINDOW = 2000  # characters of body text scanned
MAX_DOMAINS = 3

_OPPORTUNITY_TITLE = re.compile(
    r"\b(internships?|fellowships?|hackathons?|summer school|winter school|scholarships?|call for (applications|proposals|participation)|"
    r"applications? (are )?(now )?open|apply (now|by)|registrations? (are )?(now )?open|student (programme|program)|datathon)\b", re.I)
_RELEASE_TITLE = re.compile(
    r"\b(releas(e|es|ed|ing)|announcing|introducing|launch(es|ed|ing)?|now available|general availability|open[- ]sourc(es|ed|ing))\b|\bv?\d+\.\d+(\.\d+)?\b", re.I)
_POST_TITLE = re.compile(r"^(show hn|ask hn|tell hn)\b", re.I)

KINDS = ("paper", "release", "repo", "article", "news", "post", "opportunity", "announcement")


def _phrase_pattern(phrases: list[str]) -> re.Pattern | None:
    """One alternation matching any phrase as a whole word or phrase (so 'rag' does not match 'storage')."""
    cleaned = sorted({phrase.strip().lower() for phrase in phrases if phrase.strip()}, key=len, reverse=True)
    if not cleaned:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(phrase) for phrase in cleaned) + r")(?![a-z0-9])")


@dataclass
class DomainRule:
    slug: str
    keywords: re.Pattern | None
    arxiv: tuple[str, ...]
    opportunities: bool


@dataclass
class Lexicon:
    rules: list[DomainRule] = field(default_factory=list)

    @classmethod
    def load(cls, path=TAXONOMY_FILE) -> "Lexicon":
        return cls([
            DomainRule(d["slug"], _phrase_pattern(d.get("keywords", [])), tuple(d.get("arxiv", [])), bool(d.get("opportunities")))
            for d in load_toml(path).get("domain", [])
        ])

    def opportunity_domains(self) -> set[str]:
        return {rule.slug for rule in self.rules if rule.opportunities}

    def domain_scores(self, title: str, text: str, source_domains: list[str], general: bool, arxiv_categories: list[str]) -> dict[str, float]:
        title_l, text_l = title.lower(), (text or "")[:TEXT_WINDOW].lower()
        scores: dict[str, float] = {}
        for rule in self.rules:
            score = 0.0
            if rule.keywords:
                in_title = len(set(rule.keywords.findall(title_l)))
                in_text = len(set(rule.keywords.findall(text_l)))
                score += min(1.0, 0.45 * in_title + 0.12 * in_text)
            for position, category in enumerate(arxiv_categories):
                if any(category == prefix or category.startswith(prefix) for prefix in rule.arxiv):
                    score += 1.0 if position == 0 else 0.4
                    break
            if rule.slug in source_domains:
                # A focused source vouches for its space; a mixed source (Hacker News) only offers a fallback.
                score += 0.15 if general else (0.9 if rule.slug == source_domains[0] else 0.6)
            if score > 0:
                scores[rule.slug] = round(score, 3)
        return scores


def pick_domains(scores: dict[str, float], fallback: list[str]) -> list[tuple[str, float]]:
    """The item's spaces, primary first, each with a 0-1 weight. Always at least one, so no item is unreachable."""
    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    if not ranked or ranked[0][1] < 0.3:
        base = fallback[0] if fallback else "science-tech"
        return [(base, 0.3)]
    top = ranked[0][1]
    chosen = [(slug, score) for slug, score in ranked if score >= max(0.42, 0.5 * top)][:MAX_DOMAINS]
    return [(slug, round(min(1.0, score / max(top, 1.0)), 2)) for slug, score in chosen]


def detect_kind(*, hint: str | None, source_kind: str, purpose: str, title: str, url: str, primary_domain: str, opportunity_domains: set[str]) -> str:
    if hint and hint != "article":
        return hint
    if source_kind in ("arxiv", "hf_papers") or "arxiv.org/abs/" in url:
        return "paper"
    if source_kind == "github":
        return "repo"
    if purpose == "opportunities" or _OPPORTUNITY_TITLE.search(title):
        return "opportunity"
    if source_kind == "gmail":
        return "announcement"
    if _POST_TITLE.search(title):
        return "post"
    if _RELEASE_TITLE.search(title):
        return "release"
    return "article"


# ---------- topics ----------


@dataclass
class TopicMatcher:
    """Finds known topics in text. Built once per run from the topics table.

    Short all-capital aliases (RAG, RL, CTF) are matched case-sensitively so they do not fire on ordinary words.
    Model-discovered topics are only matched when they are specific enough (an entity, or two or more words)."""

    patterns: list[tuple[int, re.Pattern, re.Pattern | None]] = field(default_factory=list)

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "TopicMatcher":
        matcher = cls()
        for row in conn.execute("SELECT id, name, aliases, origin, kind FROM topics"):
            names = [row["name"]] + json.loads(row["aliases"])
            if row["origin"] == "discovered" and row["kind"] == "topic" and len(row["name"].split()) < 2:
                continue
            loose = [n for n in names if not (len(n) <= 4 and n.isupper())]
            strict = [n for n in names if len(n) <= 4 and n.isupper()]
            loose_pattern = _phrase_pattern(loose)
            strict_pattern = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(n) for n in strict) + r")(?![A-Za-z0-9])") if strict else None
            if loose_pattern or strict_pattern:
                matcher.patterns.append((row["id"], loose_pattern, strict_pattern))
        return matcher

    def match(self, title: str, text: str) -> dict[int, float]:
        """topic id -> weight (1.0 for a title match, 0.6 for a body-only match)."""
        body = (text or "")[:TEXT_WINDOW]
        title_l, body_l = title.lower(), body.lower()
        found: dict[int, float] = {}
        for topic_id, loose, strict in self.patterns:
            if (loose and loose.search(title_l)) or (strict and strict.search(title)):
                found[topic_id] = 1.0
            elif (loose and loose.search(body_l)) or (strict and strict.search(body)):
                found[topic_id] = 0.6
        return found


# ---------- applying it ----------


def classify_new(conn: sqlite3.Connection, lexicon: Lexicon | None = None, *, limit: int = 5000) -> int:
    """Classify every item at stage 'new'. Returns how many were classified. Commits once."""
    rows = conn.execute(
        """SELECT i.*, s.kind AS source_kind, s.domains AS source_domains, s.general, s.purpose
           FROM items i LEFT JOIN sources s ON s.id = i.source_id
           WHERE i.stage = 'new' ORDER BY i.id LIMIT ?""", (limit,)
    ).fetchall()
    if not rows:
        return 0
    lexicon = lexicon or Lexicon.load()
    matcher = TopicMatcher.load(conn)
    domain_ids = {row["slug"]: row["id"] for row in conn.execute("SELECT id, slug FROM domains")}
    opportunity_domains = lexicon.opportunity_domains()

    for row in rows:
        signals = json.loads(row["signals"])
        source_domains = json.loads(row["source_domains"] or "[]")
        scores = lexicon.domain_scores(row["title"], row["content"] or "", source_domains, bool(row["general"]), signals.get("categories") or [])
        domains = [(slug, weight) for slug, weight in pick_domains(scores, source_domains) if slug in domain_ids]
        if not domains:
            domains = [(next(iter(domain_ids)), 0.3)]
        kind = detect_kind(hint=row["kind"], source_kind=row["source_kind"] or "", purpose=row["purpose"] or "news", title=row["title"],
                           url=row["canonical_url"], primary_domain=domains[0][0], opportunity_domains=opportunity_domains)
        conn.execute("DELETE FROM item_domains WHERE item_id = ?", (row["id"],))
        conn.executemany("INSERT INTO item_domains (item_id, domain_id, weight) VALUES (?, ?, ?)",
                         [(row["id"], domain_ids[slug], weight) for slug, weight in domains])
        conn.executemany("INSERT OR REPLACE INTO item_topics (item_id, topic_id, weight) VALUES (?, ?, ?)",
                         [(row["id"], topic_id, weight) for topic_id, weight in matcher.match(row["title"], row["content"] or "").items()])
        conn.execute("UPDATE items SET stage = 'classified', kind = ?, domain_id = ? WHERE id = ?", (kind, domain_ids[domains[0][0]], row["id"]))
    conn.execute("UPDATE topics SET item_count = (SELECT COUNT(*) FROM item_topics WHERE topic_id = topics.id)")
    flag_clone_swarms(conn)
    conn.commit()
    return len(rows)


def retag_topics(conn: sqlite3.Connection) -> int:
    """Re-run topic matching over every stored item, for when the vocabulary changed. Only links the matcher
    made are replaced (weights 1.0 and 0.6); links a model assessment made (0.8) are left alone. Returns how
    many links changed."""
    matcher = TopicMatcher.load(conn)
    changed = 0
    for row in conn.execute("SELECT id, title, content FROM items").fetchall():
        wanted = matcher.match(row["title"], row["content"] or "")
        current = {r["topic_id"]: r["weight"] for r in conn.execute(
            "SELECT topic_id, weight FROM item_topics WHERE item_id = ? AND weight IN (1.0, 0.6)", (row["id"],))}
        for topic_id in current.keys() - wanted.keys():
            conn.execute("DELETE FROM item_topics WHERE item_id = ? AND topic_id = ?", (row["id"], topic_id))
            changed += 1
        for topic_id, weight in wanted.items():
            if topic_id not in current:
                changed += conn.execute("INSERT OR IGNORE INTO item_topics (item_id, topic_id, weight) VALUES (?, ?, ?)", (row["id"], topic_id, weight)).rowcount
    conn.execute("UPDATE topics SET item_count = (SELECT COUNT(*) FROM item_topics WHERE topic_id = topics.id)")
    conn.commit()
    return changed


CLONE_SWARM_MIN = 3


def flag_clone_swarms(conn: sqlite3.Connection) -> int:
    """Mark repositories that share one name across several owners ("alice/Some-App", "bob/Some-App", ...).

    That pattern on GitHub's "new and starred" search is star-farmed spam or malware bait, and it is exactly what a
    popularity signal rewards. It is a fact code can see, so it is handled here rather than left to a model: the
    count is recorded in the item's signals, and scoring keeps flagged items off Home and out of notifications
    (they stay in the feed, labelled, like anything else that is muted)."""
    names: dict[str, list[int]] = {}
    for row in conn.execute("SELECT id, title FROM items WHERE kind = 'repo' AND title LIKE '%/%'"):
        owner, _, name = row["title"].partition("/")
        if owner and name:
            names.setdefault(name.strip().lower(), []).append(row["id"])
    flagged = 0
    for ids in names.values():
        if len(ids) >= CLONE_SWARM_MIN:
            conn.executemany("UPDATE items SET signals = json_set(signals, '$.clone_swarm', ?) WHERE id = ?", [(len(ids), item_id) for item_id in ids])
            flagged += len(ids)
    return flagged
