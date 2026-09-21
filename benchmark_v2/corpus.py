"""Build the Benchmark v2 corpus: 160 new items sampled from what PIA's sources would have returned over a past period, plus
40 repeats drawn from the frozen v1 item snapshot.

INDEPENDENCE. Selection uses IDENTITY ONLY. The eligible pool is sorted by canonical URL and sampled uniformly at random with a
fixed seed; nothing about an item's title, text, popularity or apparent interest is read when choosing it. The repeats are drawn
from the ids in the facts-only v1 snapshot; v1 labels, scores, ranks, arms and reports are never opened (a test enforces it).

SAME MACHINERY. arXiv, Hacker News and RSS use the production adapters unchanged (they accept a window). GitHub and Hugging Face
Daily Papers cannot fetch a past window as written (GitHub sends no upper date bound; HF always returns the latest day), so
`HistoricalGitHub` and `HistoricalHFPapers` reuse the production parser, query, star/upvote filters and result caps and change only
the date parameters of the request. Items are stored with PIA's own `store_items`, so canonical-URL identity and the merging of
signals from several sources are production's.
"""

import hashlib
import json
import random
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from benchmark_v2 import common as c
from benchmarks.common import BenchmarkError, read_json, write_json_atomic
from pia.db import store_items
from pia.history import DatabaseMissing, connect_readonly
from pia.http import SourceError, get_with_retry
from pia.models import ensure_utc
from pia.normalize import canonicalize_url
from pia.sources.base import Source, parse_iso, parse_or_raise
from pia.sources.github import API_URL as GITHUB_API
from pia.sources.github import GitHubSource, parse_github
from pia.sources.hf_papers import API_URL as HF_API
from pia.sources.hf_papers import HFPapersSource, parse_hf_papers

# Same fields, same order of meaning as the v1 snapshot (benchmarks/snapshot.py), so the v1 loader and labelling screen work unchanged.
ITEM_FIELDS = ("id", "source", "external_id", "canonical_url", "url", "title", "content_raw", "published_at", "discovered_at", "signals")


def compute_corpus_id(items: list[dict]) -> str:
    """A short content hash of the item list. Identical to the v1 snapshot's identity function (a test compares them)."""
    canonical = json.dumps(items, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# ---------- the period ----------


def windows(start: datetime, end: datetime, days: int) -> list[tuple[datetime, datetime]]:
    step = timedelta(days=days)
    out, cursor = [], start
    while cursor < end:
        out.append((cursor, min(cursor + step, end)))
        cursor += step
    return out


# ---------- historical variants of the two adapters that cannot take a past window ----------


@dataclass
class HistoricalGitHub:
    """The production GitHub source with an UPPER date bound added: same query, star filter, sort and cap; same parser."""

    source: GitHubSource
    name: str = "github"

    def fetch(self, client: httpx.Client, since: datetime, until: datetime):
        stamp = lambda d: ensure_utc(d).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        params = {
            "q": f"{self.source.query} created:{stamp(since)}..{stamp(until - timedelta(seconds=1))} stars:>={self.source.min_stars}",
            "sort": "stars",
            "order": "desc",
            "per_page": self.source.max_results,
        }
        return parse_or_raise(self.name, parse_github, get_with_retry(client, GITHUB_API, params=params).text)


@dataclass
class HistoricalHFPapers:
    """The production HF Daily Papers source asked one day at a time (the API's `date` parameter); same parser, same upvote
    filter, same 'featured inside the window' rule."""

    source: HFPapersSource
    name: str = "hf_papers"

    def fetch(self, client: httpx.Client, since: datetime, until: datetime):
        kept, day = [], ensure_utc(since).replace(hour=0, minute=0, second=0, microsecond=0)
        while day < until:
            text = get_with_retry(client, HF_API, params={"date": day.date().isoformat(), "limit": self.source.max_results}).text
            for item in parse_or_raise(self.name, parse_hf_papers, text):
                if item.signals["upvotes"] < self.source.min_upvotes:
                    continue
                featured = parse_iso(item.signals["featured_at"]) or item.published_at
                if featured is None or since <= featured <= until:
                    kept.append(item)
            day += timedelta(days=1)
        return kept


def replayable(sources: list[Source]) -> list[Source]:
    """Production sources, except that the two which cannot fetch a past window are replaced by their historical variants."""
    out: list[Source] = []
    for source in sources:
        if isinstance(source, GitHubSource):
            out.append(HistoricalGitHub(source))
        elif isinstance(source, HFPapersSource):
            out.append(HistoricalHFPapers(source))
        else:
            out.append(source)
    return out


# ---------- the pool ----------


@dataclass
class PoolReport:
    fetches: dict[tuple[str, int], int] = field(default_factory=dict)  # (source, window index) -> items returned
    first_window: dict[str, int] = field(default_factory=dict)  # canonical URL -> first window that returned it
    rejected: int = 0  # items PIA's store_items refused (rule E3)

    def summary(self) -> dict:
        per_source: dict[str, int] = {}
        for (name, _), n in self.fetches.items():
            per_source[name] = per_source.get(name, 0) + n
        return {"fetches": len(self.fetches), "returned_per_source": per_source, "rejected_by_store": self.rejected}


def collect_pool(
    conn: sqlite3.Connection,
    client: httpx.Client | None,
    sources: list[Source],
    period: list[tuple[datetime, datetime]],
    now: datetime,
    *,
    pause: Callable[[float], None] = time.sleep,
) -> PoolReport:
    """Fetch every window for every source, chronologically, sources in configuration order, and store with production code.

    Completeness rule: every fetch must succeed. A failure stops the build; the pool is never completed by dropping the failed part."""
    report, first = PoolReport(), True
    for index, (since, until) in enumerate(period):
        for source in sources:
            if not first and c.PAUSE_SECONDS.get(source.name):
                pause(c.PAUSE_SECONDS[source.name])
            first = False
            try:
                items = source.fetch(client, since, until)
            except (SourceError, BenchmarkError) as exc:
                raise BenchmarkError(f"{source.name} failed in window {index} ({since:%Y-%m-%d} to {until:%Y-%m-%d}): {exc}. Rerun the build; the pool must be complete.") from exc
            stored = store_items(conn, items, now)
            report.rejected += stored.rejected
            report.fetches[(source.name, index)] = len(items)
            for item in items:
                try:
                    report.first_window.setdefault(canonicalize_url(item.url), index)
                except ValueError:
                    pass
    return report


# ---------- identity: what already belongs to v1 ----------


@dataclass
class V1Index:
    canonical: set[str]
    external: set[tuple[str, str]]


def v1_identities(items_path, db_path=None) -> V1Index:
    """Identities of every v1 corpus item (from the facts-only snapshot) and, if given, of every item in the real database."""
    items = read_json(items_path)["items"]
    canonical = {i["canonical_url"] for i in items}
    external = {(i["source"], i["external_id"]) for i in items}
    if db_path is not None:
        try:
            conn = connect_readonly(db_path)
        except DatabaseMissing:
            return V1Index(canonical, external)
        try:
            for row in conn.execute("SELECT canonical_url, source, external_id FROM items"):
                canonical.add(row["canonical_url"])
                external.add((row["source"], row["external_id"]))
        finally:
            conn.close()
    return V1Index(canonical, external)


def eligible_pool(conn: sqlite3.Connection, index: V1Index) -> tuple[list[sqlite3.Row], dict[str, int]]:
    """Every pool item that is not excluded. Exclusion is by IDENTITY only (E1 canonical URL, E2 source + external id in v1);
    E3 (rejected by store_items) happens at collection. Nothing about content, topic or popularity excludes an item."""
    excluded = {"E1_canonical_url_in_v1": 0, "E2_source_external_id_in_v1": 0}
    rows = []
    for row in conn.execute("SELECT * FROM items ORDER BY canonical_url"):
        if row["canonical_url"] in index.canonical:
            excluded["E1_canonical_url_in_v1"] += 1
        elif (row["source"], row["external_id"]) in index.external:
            excluded["E2_source_external_id_in_v1"] += 1
        else:
            rows.append(row)
    return rows, excluded


def sample_new(rows: list[sqlite3.Row], n: int, seed: int) -> list[sqlite3.Row]:
    """A uniform random sample without replacement. Only the sorted canonical URLs determine which items are drawn."""
    ordered = sorted(rows, key=lambda r: r["canonical_url"])
    if len(ordered) < n:
        raise BenchmarkError(f"only {len(ordered)} eligible items in the pool; {n} are needed. Nothing is padded or relaxed.")
    return random.Random(seed).sample(ordered, n)


def draw_repeats(v1_items: list[dict], n: int, seed: int) -> list[dict]:
    """A uniform random sample of v1 items. Uses the ids alone; labels are never read."""
    by_id = {i["id"]: i for i in v1_items}
    chosen = random.Random(seed).sample(sorted(by_id), n)
    return [by_id[i] for i in chosen]


def assert_absent(new_items: list[dict], index: V1Index) -> dict:
    clash_url = [i for i in new_items if i["canonical_url"] in index.canonical]
    clash_id = [i for i in new_items if (i["source"], i["external_id"]) in index.external]
    if clash_url or clash_id:
        raise BenchmarkError(f"{len(clash_url) + len(clash_id)} new items are present in the v1 corpus (by canonical URL or source + external id); the corpus is invalid")
    return {"new_items": len(new_items), "canonical_url_overlap": 0, "source_external_id_overlap": 0, "passed": True}


# ---------- the corpus ----------


@dataclass
class Corpus:
    items: list[dict]
    manifest: dict
    corpus_id: str


def _item_from_row(row: sqlite3.Row) -> dict:
    item = {f: row[f] for f in ITEM_FIELDS}
    item["signals"] = json.loads(row["signals"])
    return item


def build_corpus(
    pool: sqlite3.Connection,
    v1_items_path,
    *,
    first_window: dict[str, int],
    n_new: int,
    n_repeat: int,
    now: datetime,
    collected: dict,
    v1_db_path=None,
    sample_seed: int = c.SAMPLE_SEED,
    repeat_seed: int = c.REPEAT_SEED,
) -> Corpus:
    v1_items = read_json(v1_items_path)["items"]
    index = v1_identities(v1_items_path, v1_db_path)
    rows, excluded = eligible_pool(pool, index)
    new_items = [_item_from_row(r) for r in sample_new(rows, n_new, sample_seed)]
    repeats = draw_repeats(v1_items, n_repeat, repeat_seed)
    absence = assert_absent(new_items, index)

    # Neutral ids: a hash of the canonical URL, so an id says nothing about where an item came from.
    tagged = [(i, "new", None) for i in new_items] + [(i, "repeat", i["id"]) for i in repeats]
    tagged.sort(key=lambda t: hashlib.sha256((c.ID_SALT + t[0]["canonical_url"]).encode("utf-8")).hexdigest())
    items, origin_by_id, new_meta, repeat_meta = [], {}, [], []
    for v2_id, (item, origin, v1_id) in enumerate(tagged, start=1):
        items.append({**{k: item[k] for k in ITEM_FIELDS if k != "id"}, "id": v2_id})
        origin_by_id[str(v2_id)] = origin
        record = {"v2_id": v2_id, "canonical_url": item["canonical_url"], "source": item["source"], "external_id": item["external_id"]}
        if origin == "new":
            new_meta.append({**record, "first_window": first_window.get(item["canonical_url"])})
        else:
            repeat_meta.append({**record, "v1_item_id": v1_id})
    corpus_id = compute_corpus_id(items)
    manifest = {
        "benchmark_version": c.BENCHMARK_VERSION,
        "corpus_id": corpus_id,
        "created_at": now.isoformat(),
        "period": {"start": c.WINDOW_START.isoformat(), "end": c.WINDOW_END.isoformat(), "window_days": c.WINDOW_DAYS, "windows": len(windows(c.WINDOW_START, c.WINDOW_END, c.WINDOW_DAYS))},
        "seeds": {"sample": sample_seed, "repeats": repeat_seed, "queue": c.QUEUE_SEED},
        "collected": collected,
        "pool": {"eligible": len(rows), "excluded": excluded},
        "absence_check": absence,
        "origin_by_id": origin_by_id,
        "new": new_meta,
        "repeats": repeat_meta,
    }
    return Corpus(items, manifest, corpus_id)


def write_corpus(corpus: Corpus, data_dir) -> None:
    """items.json is byte-compatible with the v1 snapshot loader; the manifest (origins, repeat identities) is internal."""
    items_path = data_dir / c.ITEMS_FILE
    if items_path.exists():
        raise BenchmarkError(f"{items_path} already exists. The corpus is frozen once written; delete the data folder only to start Benchmark v2 over.")
    write_json_atomic(items_path, {"format": 1, "snapshot_id": corpus.corpus_id, "exported_at": corpus.manifest["created_at"], "source_schema": 0, "items": corpus.items})
    write_json_atomic(data_dir / c.MANIFEST_FILE, corpus.manifest)
