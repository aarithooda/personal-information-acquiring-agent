"""Deterministic ranking of triaged items.

score = LLM importance (1-5)
      + popularity percentile (0-1): how the item's best signal compares with the *other
        items from that same source in this window*, so there are no magic thresholds
      + cross-source boost (0-1): being reported by several independent sources

LLM scores are only trusted for ordering. Measured on real data they are generous and
uncalibrated, so we never compare them against a fixed cutoff.
"""

import json
import sqlite3
from bisect import bisect_left, bisect_right

POPULARITY_METRICS = {"hn": "points", "hf_papers": "upvotes", "github": "stars"}
CROSS_SOURCE_BOOST = 0.5  # per additional source, capped at 1.0


def _metric(signals: dict, source: str) -> float | None:
    data = signals.get(source)
    value = data.get(POPULARITY_METRICS[source]) if isinstance(data, dict) else None
    return value if isinstance(value, (int, float)) else None


def rank_items(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    signals = [json.loads(row["signals"]) for row in rows]

    # Per source, the sorted values seen in this batch (the reference for percentiles).
    population = {
        source: sorted(v for s in signals if (v := _metric(s, source)) is not None)
        for source in POPULARITY_METRICS
    }

    def percentile(source: str, value: float) -> float:
        values = population[source]
        below = bisect_left(values, value)
        equal = bisect_right(values, value) - below
        return (below + 0.5 * equal) / len(values)

    def score(row: sqlite3.Row, sig: dict) -> float:
        popularity = max(
            (percentile(s, v) for s in POPULARITY_METRICS if (v := _metric(sig, s)) is not None),
            default=0.0,
        )
        boost = min(1.0, CROSS_SOURCE_BOOST * (len(sig) - 1)) if sig else 0.0
        return row["importance"] + popularity + boost

    scored = [(score(row, sig), row) for row, sig in zip(rows, signals)]
    recency = lambda pair: pair[1]["published_at"] or pair[1]["discovered_at"]  # noqa: E731
    scored.sort(key=recency, reverse=True)  # stable sorts: newest first among equal scores
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in scored]
