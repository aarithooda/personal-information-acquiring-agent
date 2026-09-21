"""The benchmark's metric functions, checked against small rankings whose answers can be worked out by hand.

A ranking is a list of GRADES in the order the system ranked the items: 2 = SHOW, 1 = MAYBE, 0 = SKIP.
"""

import math

import pytest

from benchmarks import metrics as m

RANKED = [2, 0, 2, 1, 0, 0]  # SHOW at ranks 1 and 3, MAYBE at 4


def test_precision_recall_and_capture_at_k_for_strict_shows():
    s = m.summarize(RANKED, ks=(2, 4))
    assert s["precision@2"] == 0.5 and s["recall@2"] == 0.5
    assert s["precision@4"] == 0.5 and s["recall@4"] == 1.0
    assert s["hits@4"] == 2


def test_capture_is_relative_to_the_best_possible_at_k():
    # 2 SHOW items exist, so at K=1 the best possible is 1 hit: capturing it is a perfect score, not 50% recall.
    s = m.summarize(RANKED, ks=(1, 4))
    assert s["recall@1"] == 0.5
    assert s["capture@1"] == 1.0
    assert s["capture@4"] == 1.0


def test_lenient_metrics_count_maybe_as_a_hit():
    s = m.summarize(RANKED, ks=(4,))
    assert s["recall_lenient@4"] == 1.0  # 3 of 3
    assert s["precision_lenient@4"] == 0.75


def test_average_precision_rewards_finding_positives_early():
    assert m.summarize(RANKED)["ap"] == pytest.approx((1 / 1 + 2 / 3) / 2)
    assert m.summarize([2, 2, 0, 0])["ap"] == 1.0
    assert m.summarize([0, 0, 2, 2])["ap"] < m.summarize([2, 0, 2, 0])["ap"]


def test_ndcg_uses_graded_gains_and_a_log_discount():
    dcg = 2 / math.log2(2) + 0 + 2 / math.log2(4) + 1 / math.log2(5)
    ideal = 2 / math.log2(2) + 2 / math.log2(3) + 1 / math.log2(4)
    assert m.summarize(RANKED, ks=(4,))["ndcg@4"] == pytest.approx(dcg / ideal)
    assert m.summarize([2, 1, 0, 0], ks=(4,))["ndcg@4"] == pytest.approx(1.0)


def test_auc_can_exclude_maybe_or_treat_it_as_not_show():
    s = m.summarize(RANKED)
    assert s["auc_show_vs_skip"] == pytest.approx(5 / 6)  # MAYBE ignored: 5 of 6 (SHOW, SKIP) pairs ordered correctly
    assert s["auc_show_vs_rest"] == pytest.approx(7 / 8)  # MAYBE counted as a negative: 7 of 8 pairs


def test_spearman_is_one_for_a_perfect_ranking_and_minus_one_for_the_worst():
    assert m.summarize([2, 1, 0])["spearman"] == pytest.approx(1.0)
    assert m.summarize([0, 1, 2])["spearman"] == pytest.approx(-1.0)


def test_metrics_that_cannot_be_computed_are_none_not_zero():
    s = m.summarize([0, 0, 0], ks=(2,))
    assert s["recall@2"] is None and s["ap"] is None and s["auc_show_vs_skip"] is None and s["capture@2"] is None
    assert s["precision@2"] == 0.0  # precision is defined: nothing in the top 2 was a SHOW
    assert m.summarize([2, 2], ks=(2,))["auc_show_vs_skip"] is None  # no negatives to compare against
    assert m.summarize([1, 1, 1])["spearman"] is None  # no variation in the labels


def test_k_larger_than_the_list_uses_the_whole_list():
    s = m.summarize([2, 0], ks=(32,))
    assert s["precision@32"] == 0.5 and s["recall@32"] == 1.0


def test_on_a_partial_label_set_k_is_scaled_to_the_share_of_items_labelled():
    assert m.effective_k(16, n_labelled=186, n_total=186) == 16
    assert m.effective_k(16, n_labelled=93, n_total=186) == 8
    assert m.effective_k(4, n_labelled=10, n_total=186) == 1  # never below 1
    s = m.summarize([2, 0, 0, 0], ks=(16,), n_total=8)  # half the items labelled -> K becomes 8, i.e. the whole list
    assert s["hits@16"] == 1


# ---------- uncertainty: the bootstrap ----------


def _positions(order):
    return {item: pos for pos, item in enumerate(order)}


def test_bootstrap_is_reproducible_and_brackets_the_point_estimate():
    items = [f"i{n}" for n in range(40)]
    grades = {item: (2 if n % 4 == 0 else 0) for n, item in enumerate(items)}
    order = sorted(items, key=lambda i: (grades[i] != 2, i))  # SHOW items first: a perfect ranking, then noise
    kwargs = dict(items=items, grades=grades, positions={"a": _positions(order)}, metrics=("ap", "recall@10"), n_boot=200, seed=7)
    first, second = m.bootstrap_metrics(**kwargs), m.bootstrap_metrics(**kwargs)
    assert first == second
    lo, hi = m.percentile_ci(first["a"]["ap"])
    assert lo == hi == 1.0  # a perfect ranking stays perfect under resampling


def test_bootstrap_resamples_items_and_uses_the_same_resample_for_every_arm():
    items = [f"i{n}" for n in range(30)]
    grades = {item: (2 if n < 10 else 0) for n, item in enumerate(items)}
    good = _positions(items)  # SHOW items first
    bad = _positions(list(reversed(items)))
    out = m.bootstrap_metrics(items, grades, {"good": good, "bad": bad}, metrics=("auc_show_vs_skip",), n_boot=100, seed=1)
    assert len(out["good"]["auc_show_vs_skip"]) == len(out["bad"]["auc_show_vs_skip"]) == 100
    # Paired: on every resample the reversed ranking is exactly the mirror image of the good one.
    for g, b in zip(out["good"]["auc_show_vs_skip"], out["bad"]["auc_show_vs_skip"]):
        if g is not None:
            assert g + b == pytest.approx(1.0)


def test_paired_delta_reports_the_difference_and_how_often_b_beats_a():
    delta = m.paired_delta([0.5, 0.6, 0.7, None], [0.6, 0.6, 0.9, 0.9])
    assert delta["n"] == 3  # resamples where either metric was undefined are dropped, and we say how many remain
    assert delta["mean"] == pytest.approx(0.1)
    assert delta["p_b_better"] == pytest.approx(2 / 3)
    assert delta["p_tie"] == pytest.approx(1 / 3)


def test_percentile_ci_ignores_undefined_resamples():
    assert m.percentile_ci([None, 1.0, 2.0, 3.0]) == (1.0, 3.0)
    assert m.percentile_ci([None, None]) == (None, None)


# ---------- label reliability (test-retest) ----------


def test_weighted_kappa_is_one_for_identical_labels_and_zero_for_chance_agreement():
    assert m.weighted_kappa([(2, 2), (1, 1), (0, 0), (0, 0)]) == pytest.approx(1.0)
    assert m.weighted_kappa([(2, 2), (2, 0), (0, 0), (0, 0)]) == pytest.approx(0.5)  # worked out by hand: (0.75-0.5)/0.5
    assert m.weighted_kappa([(2, 0), (0, 2)]) < 0  # systematic disagreement is worse than chance


def test_weighted_kappa_penalises_show_vs_skip_more_than_show_vs_maybe():
    near = m.weighted_kappa([(2, 1), (1, 1), (0, 0), (0, 0), (2, 2)])
    far = m.weighted_kappa([(2, 0), (1, 1), (0, 0), (0, 0), (2, 2)])
    assert near > far


def test_kappa_is_undefined_when_there_is_no_variation_or_no_pairs():
    assert m.weighted_kappa([]) is None
    assert m.weighted_kappa([(0, 0), (0, 0)]) is None


def test_exact_agreement():
    assert m.exact_agreement([(2, 2), (1, 0), (0, 0), (2, 2)]) == 0.75
    assert m.exact_agreement([]) is None
