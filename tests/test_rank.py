import json
import sqlite3

from pia.briefing.rank import rank_items


def row(id_, importance, signals=None, published="2026-09-18T00:00:00+00:00"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT ? AS id, ? AS importance, ? AS signals, ? AS published_at, ? AS discovered_at",
        (id_, importance, json.dumps(signals or {}), published, published),
    ).fetchone()


def ids(rows) -> list[int]:
    return [r["id"] for r in rows]


def test_higher_importance_ranks_first_when_there_are_no_other_signals():
    assert ids(rank_items([row(1, 2), row(2, 4), row(3, 3)])) == [2, 3, 1]


def test_popularity_is_relative_to_the_source_not_a_fixed_threshold():
    # Same importance; 300 points is the top of THIS window's HN items, 50 the bottom.
    hot = row(1, 3, {"hn": {"points": 300}})
    warm = row(2, 3, {"hn": {"points": 120}})
    cold = row(3, 3, {"hn": {"points": 50}})
    assert ids(rank_items([cold, hot, warm])) == [1, 2, 3]


def test_the_same_number_means_different_things_in_different_windows():
    quiet_week = [row(1, 3, {"hn": {"points": 120}}), row(2, 3, {"hn": {"points": 100}})]
    busy_week = [row(1, 3, {"hn": {"points": 120}}), row(2, 3, {"hn": {"points": 900}})]
    assert ids(rank_items(quiet_week))[0] == 1  # 120 is the best of a quiet week
    assert ids(rank_items(busy_week))[0] == 2  # ... and merely second best of a busy one


def test_being_seen_on_several_sources_boosts_an_item():
    lone = row(1, 3, {"arxiv": {}})
    shared = row(2, 3, {"arxiv": {}, "hf_papers": {"upvotes": 10}, "hn": {"points": 10}})
    assert ids(rank_items([lone, shared]))[0] == 2


def test_items_without_any_popularity_data_are_ranked_by_importance_alone():
    assert ids(rank_items([row(1, 3, {"arxiv": {}}), row(2, 4, {"quanta": {}})])) == [2, 1]


def test_ties_are_broken_by_recency():
    older = row(1, 3, published="2026-09-10T00:00:00+00:00")
    newer = row(2, 3, published="2026-09-19T00:00:00+00:00")
    assert ids(rank_items([older, newer])) == [2, 1]


def test_ranking_an_empty_list_is_fine():
    assert rank_items([]) == []
