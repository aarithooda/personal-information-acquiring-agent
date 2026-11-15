"""Scores: importance, relevance, popularity, and the rank the Important view sorts by.

Two different questions are kept as two numbers (ARCHITECTURE.md, A9):
  importance  how significant is this in its field?   model judgement when there is one, blended with what the
              world signalled (source trust, popularity, several sources reporting it)
  relevance   how much does THIS owner care?          the interest profile: space weight, topic levels, learned
              affinity, and an optional personal-fit judgement

Everything here is arithmetic over stored inputs, so changing a constant and calling `rescore` re-ranks the whole
library without a single model call. The constants are named priors, not fitted values: no labelled data for the
platform's ranking exists yet (the v1 benchmark corpus is spent and must not be used to tune them).
"""

import json
import sqlite3
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from pia.store.db import days_ago, iso, parse_ts

W_IMPORTANCE, W_RELEVANCE, W_POPULARITY = 0.45, 0.40, 0.15

# How much an interest level says about relevance on its own. None = a topic the owner has not rated.
LEVEL_VALUE = {3: 0.9, 2: 0.74, 1: 0.52, None: 0.36, -1: 0.08}
NO_TOPIC_VALUE = 0.33  # an item that matches no known topic: neither evidence for nor against
AFFINITY_REACH = 0.25  # the most learned affinity can move an UNRATED topic's value
AFFINITY_REACH_RATED = 0.07  # for a topic the owner rated: a nudge within its level, never enough to cross into another
MODEL_SHARE = 0.65  # weight of the model's significance judgement against the deterministic signals
FIT_SHARE = 0.35  # weight of an optional personal-fit judgement inside relevance
MUTED_FACTOR = 0.15
OPPORTUNITY_FLOOR = 0.45  # an opportunity's "significance" is not what matters about it; do not let it sink
FIRST_YEAR_BONUS = 0.15

HALF_LIFE_DAYS = 21.0  # recency: an item loses half of the decaying part of its rank every three weeks...
RANK_FLOOR = 0.35  # ...but never falls below this share of its score: old is not the same as unimportant
BOOST_HALF_LIFE_DAYS = 14.0
MAX_CONTEXT_BOOST = 0.25

POPULARITY_METRICS = ("points", "stars", "upvotes", "registrations")
POPULARITY_WINDOW_DAYS = 45
NEUTRAL_POPULARITY = 0.5  # a curated source with no public metric: being published there is itself a selection
GENERAL_NO_METRIC = 0.3


@dataclass
class Scores:
    importance: float
    relevance: float
    popularity: float
    score: float
    muted: bool


class PopularityTable:
    """Per metric, the sorted values seen recently: the reference population for percentiles, so there are no
    absolute thresholds ("100 points is a lot") that drift as a source grows."""

    def __init__(self, conn: sqlite3.Connection, now: datetime):
        self.values: dict[str, list[float]] = {metric: [] for metric in POPULARITY_METRICS}
        for (raw,) in conn.execute("SELECT signals FROM items WHERE acquired_at >= ? AND signals != '{}'", (days_ago(now, POPULARITY_WINDOW_DAYS),)):
            signals = json.loads(raw)
            for metric in POPULARITY_METRICS:
                value = signals.get(metric)
                if isinstance(value, (int, float)):
                    self.values[metric].append(float(value))
        for values in self.values.values():
            values.sort()

    def percentile(self, signals: dict, *, general: bool) -> float:
        best = None
        for metric in POPULARITY_METRICS:
            value = signals.get(metric)
            population = self.values[metric]
            if isinstance(value, (int, float)) and population:
                below = bisect_left(population, value)
                equal = bisect_right(population, value) - below
                best = max(best or 0.0, (below + 0.5 * equal) / len(population))
        if best is None:
            return GENERAL_NO_METRIC if general else NEUTRAL_POPULARITY
        return best


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def topic_value(level: int | None, affinity: float) -> float:
    reach = AFFINITY_REACH if level is None else AFFINITY_REACH_RATED  # what the owner said outranks what was inferred
    return _clamp(LEVEL_VALUE.get(level, LEVEL_VALUE[None]) + reach * affinity)


def compute(
    *,
    kind: str,
    signals: dict,
    model_importance: float | None,
    fit: float | None,
    sightings: int,
    trust: float,
    quality: float,
    general: bool,
    domain_weight: float,
    domain_affinity: float,
    topics: list[tuple[int | None, float, float]],  # (level, affinity, match weight)
    popularity: float,
) -> Scores:
    cross = min(1.0, 0.5 * max(0, sightings - 1))
    world = 0.40 * trust + 0.35 * popularity + 0.25 * cross
    importance = world if model_importance is None else MODEL_SHARE * model_importance + (1 - MODEL_SHARE) * world
    importance *= 1 + 0.5 * _clamp(quality, -1.0, 0.0)  # "poor quality" feedback on a source lowers its items
    if kind == "opportunity":
        importance = max(importance, OPPORTUNITY_FLOOR)

    domain_part = _clamp(domain_weight + 0.4 * domain_affinity, 0.0, 1.5) / 1.5
    live = [(level, affinity, weight) for level, affinity, weight in topics if level != -2]
    if live:
        # A title match counts in full, a body-only match a little less. One matched interest never saturates
        # the scale on its own (broad topics such as "large language models" match a great many items); an item
        # touching several strong interests earns the rest.
        values = sorted((topic_value(level, affinity) * (0.7 + 0.3 * weight) for level, affinity, weight in live), reverse=True)
        strong = sum(1 for level, _, _ in live if level is not None and level >= 2)
        topic_part = _clamp(values[0] + min(0.10, 0.05 * max(0, strong - 1)))
    else:
        topic_part = _clamp(NO_TOPIC_VALUE + 0.15 * domain_affinity)
    relevance = 0.35 * domain_part + 0.65 * topic_part
    if fit is not None:
        relevance = (1 - FIT_SHARE) * relevance + FIT_SHARE * fit
    if kind == "opportunity" and signals.get("first_year") == "yes":
        relevance = _clamp(relevance + FIRST_YEAR_BONUS)

    # Muted: the item touches a muted topic and nothing the owner rates highly. It stays in its space's feed
    # (retention is not selectivity) but is kept off Home and out of notifications.
    muted = any(level == -2 for level, _, _ in topics) and not any(level is not None and level >= 2 for level, _, _ in live)
    if muted:
        relevance *= MUTED_FACTOR
    if signals.get("clone_swarm"):  # one repository name under many owners: star-farmed spam (see classify)
        muted = True
        importance *= 0.3

    importance, relevance = _clamp(importance), _clamp(relevance)
    score = W_IMPORTANCE * importance + W_RELEVANCE * relevance + W_POPULARITY * popularity
    return Scores(round(importance, 4), round(relevance, 4), round(popularity, 4), round(score, 4), muted)


@lru_cache(maxsize=1)
def _fit_enabled() -> bool:
    from pia.intel.usage import ModelConfig  # imported here: usage depends on the model layer, scoring does not

    return ModelConfig.load().jev_enabled


def rescore(conn: sqlite3.Connection, now: datetime, *, item_ids: list[int] | None = None, use_fit: bool | None = None) -> int:
    """Recompute stored scores from stored inputs. With no ids: every classified item. Commits once.

    `use_fit`: whether stored personal-fit judgements (Jev) take part. Default: only if Jev is switched on in
    config/models.toml. While it is off, judgements stored earlier stay in the database untouched but have no
    effect on ranking, so "Groq only" is true of the scores as well as of the network calls."""
    if use_fit is None:
        use_fit = _fit_enabled()
    where, params = "i.stage != 'new'", []
    if item_ids is not None:
        if not item_ids:
            return 0
        where += f" AND i.id IN ({','.join('?' * len(item_ids))})"
        params = list(item_ids)
    rows = conn.execute(
        f"""SELECT i.id, i.kind, i.signals, i.model_importance, i.fit,
                   COALESCE(s.trust, 0.5) AS trust, COALESCE(s.quality, 0) AS quality, COALESCE(s.general, 0) AS general,
                   COALESCE(d.weight, 1.0) AS domain_weight, COALESCE(d.affinity, 0) AS domain_affinity,
                   (SELECT COUNT(*) FROM item_sources x WHERE x.item_id = i.id) AS sightings
            FROM items i LEFT JOIN sources s ON s.id = i.source_id LEFT JOIN domains d ON d.id = i.domain_id
            WHERE {where}""", params
    ).fetchall()
    if not rows:
        return 0
    topics_by_item: dict[int, list[tuple]] = {}
    topic_query = "SELECT it.item_id, t.level, t.affinity, it.weight FROM item_topics it JOIN topics t ON t.id = it.topic_id"
    if item_ids is not None:
        topic_query += f" WHERE it.item_id IN ({','.join('?' * len(item_ids))})"
    for item_id, level, affinity, weight in conn.execute(topic_query, params):
        topics_by_item.setdefault(item_id, []).append((level, affinity, weight))

    table = PopularityTable(conn, now)
    updates = []
    for row in rows:
        signals = json.loads(row["signals"])
        scores = compute(
            kind=row["kind"], signals=signals, model_importance=row["model_importance"], fit=row["fit"] if use_fit else None, sightings=row["sightings"],
            trust=row["trust"], quality=row["quality"], general=bool(row["general"]), domain_weight=row["domain_weight"],
            domain_affinity=row["domain_affinity"], topics=topics_by_item.get(row["id"], []),
            popularity=table.percentile(signals, general=bool(row["general"])),
        )
        updates.append((scores.importance, scores.relevance, scores.popularity, scores.score, int(scores.muted), row["id"]))
    conn.executemany("UPDATE items SET importance = ?, relevance = ?, popularity = ?, score = ?, muted = ? WHERE id = ?", updates)
    conn.commit()
    refresh_rank(conn, now, item_ids=item_ids)
    return len(updates)


def rank_value(score: float, sort_at: str, boost: float, boosted_at: str | None, now: datetime) -> float:
    age = max(0.0, (now - parse_ts(sort_at)).total_seconds() / 86400)
    decay = RANK_FLOOR + (1 - RANK_FLOOR) * 0.5 ** (age / HALF_LIFE_DAYS)
    lift = 0.0
    if boost and boosted_at:
        boost_age = max(0.0, (now - parse_ts(boosted_at)).total_seconds() / 86400)
        lift = boost * 0.5 ** (boost_age / BOOST_HALF_LIFE_DAYS)
    return round(score * decay + lift, 4)


def refresh_rank(conn: sqlite3.Connection, now: datetime, *, item_ids: list[int] | None = None) -> int:
    """Apply recency to every score. Run each worker cycle so the Important view ages smoothly."""
    query, params = "SELECT id, score, sort_at, context_boost, boosted_at FROM items", []
    if item_ids is not None:
        if not item_ids:
            return 0
        query += f" WHERE id IN ({','.join('?' * len(item_ids))})"
        params = list(item_ids)
    updates = [(rank_value(row["score"], row["sort_at"], row["context_boost"], row["boosted_at"], now), row["id"]) for row in conn.execute(query, params)]
    conn.executemany("UPDATE items SET rank = ? WHERE id = ?", updates)
    conn.commit()
    return len(updates)


def add_context_boost(conn: sqlite3.Connection, item_id: int, amount: float, now: datetime) -> None:
    """A later development relates to this item: lift it in Important for a while (PRD: old is not stale)."""
    conn.execute(
        "UPDATE items SET context_boost = MIN(?, context_boost + ?), boosted_at = ? WHERE id = ?",
        (MAX_CONTEXT_BOOST, amount, iso(now), item_id),
    )
