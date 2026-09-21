"""The analysis, checked on a synthetic world whose right answers are known in advance.

World: 40 items cycling through hn (title-only), arxiv, hf_papers, github. Items 0-7 are SHOW, 8-13 MAYBE, the rest SKIP.
  perfect    ranks the items in exactly the human's order
  reversed   ranks them in exactly the opposite order
  penalising ranks text items above ALL title-only ones (the failure mode found on the first real Jev run)
"""

import dataclasses
import json
from datetime import datetime, timezone

import pytest
from fakes import SmartLLM  # noqa: F401  (kept for parity with the arms tests)
from test_bench_arms import CODE, NOW, make_arm, make_snapshot, record
from test_jev_triage import PROFILE_TOML, FakeJev

from benchmarks import analysis as an
from benchmarks import arms
from benchmarks.common import BenchmarkError
from benchmarks.labelset import LabelSet
from pia.jev import triage as jt
from pia.profile import load_profile

N = 40
SHOW_IDS = {500 + i for i in range(8)}


def grade_of(i):
    return 2 if i < 8 else 1 if i < 14 else 0


def make_labels(n=N, only=None, source="frozen"):
    ids = range(n) if only is None else only
    grades = {500 + i: grade_of(i) for i in ids}
    counts = {"SHOW": sum(g == 2 for g in grades.values()), "MAYBE": sum(g == 1 for g in grades.values()), "SKIP": sum(g == 0 for g in grades.values())}
    return LabelSet(grades, n, source, "hash123", counts, retest=[], folds={i: i % 5 for i in grades})


def relevance_arm(shot, fn, name, kind="jev"):
    return make_arm(shot, lambda n, item: record("ai", 3, relevance=fn(n, item)), name=name, kind=kind)


@pytest.fixture
def world():
    shot = make_snapshot(N)
    hn = lambda item: item["source"] == "hn"  # noqa: E731
    return {
        "shot": shot,
        "arms": {
            "perfect": relevance_arm(shot, lambda n, item: 0.95 - n / 100, "perfect"),
            "reversed": relevance_arm(shot, lambda n, item: 0.05 + n / 100, "reversed"),
            "penalising": relevance_arm(shot, lambda n, item: 0.1 if hn(item) else 0.9 - n / 100, "penalising"),
        },
    }


CONFIG = an.Config(n_boot=60, seed=3)


def run(world, arm_names=("perfect", "reversed"), labels=None, config=CONFIG, baseline=None):
    arms_ = {name: world["arms"][name] for name in arm_names}
    if baseline is not None:
        config = dataclasses.replace(config, baseline=baseline)
    return an.analyze(world["shot"], labels or make_labels(), arms_, config=config, now=NOW, code=CODE)


# ---------- headline metrics ----------


def test_a_perfect_ranking_scores_perfectly_and_a_reversed_one_scores_zero_where_it_should(world):
    result = run(world)
    perfect, reverse = result["arms"]["perfect"]["metrics"], result["arms"]["reversed"]["metrics"]
    for key in ("precision@4", "recall@10", "recall@16", "ndcg@16", "ap", "auc_show_vs_skip", "auc_show_vs_rest"):
        assert perfect[key]["value"] == pytest.approx(1.0), key
    # With only three label levels (lots of ties) Spearman cannot reach 1.0 even for a perfect ranking: its ceiling is
    # below 1. What must hold is that perfect and reversed are exact mirror images and the perfect one is high.
    assert perfect["spearman"]["value"] > 0.8 and perfect["spearman"]["value"] == pytest.approx(-reverse["spearman"]["value"])
    assert reverse["recall@16"]["value"] == 0.0 and reverse["auc_show_vs_skip"]["value"] == 0.0
    assert perfect["precision@10"]["value"] == 0.8  # only 8 SHOW items exist, so 2 of the top 10 cannot be SHOW


def test_capture_shows_the_perfect_ranking_is_perfect_even_where_precision_cannot_be(world):
    perfect = run(world)["arms"]["perfect"]["metrics"]
    assert perfect["precision@10"]["value"] < 1.0 and perfect["capture@10"]["value"] == 1.0


def test_every_metric_carries_a_bootstrap_interval_that_brackets_the_estimate(world):
    m = run(world, arm_names=("penalising",))["arms"]["penalising"]["metrics"]["ap"]
    lo, hi = m["ci"]
    assert lo <= m["value"] <= hi and lo < hi


def test_the_analysis_is_reproducible(world):
    assert run(world) == run(world)


def test_without_bootstrap_there_are_no_intervals_but_everything_else_is_there(world):
    result = run(world, config=an.Config(n_boot=0))
    assert result["arms"]["perfect"]["metrics"]["ap"]["ci"] is None and result["arms"]["perfect"]["metrics"]["ap"]["value"] == 1.0


def test_the_model_only_variant_is_reported_beside_the_production_ranking(world):
    result = run(world)
    assert result["arms"]["perfect"]["model_only"]["ap"] == pytest.approx(1.0)


def test_scores_by_label_show_where_each_kind_of_item_landed(world):
    by_label = run(world)["arms"]["perfect"]["by_label"]
    assert by_label["SHOW"]["n"] == 8 and by_label["MAYBE"]["n"] == 6 and by_label["SKIP"]["n"] == 26
    assert by_label["SHOW"]["median_rank"] < by_label["MAYBE"]["median_rank"] < by_label["SKIP"]["median_rank"]


# ---------- comparing arms ----------


def test_arms_are_compared_on_the_same_resamples_against_a_baseline(world):
    result = run(world, baseline="reversed")
    (cmp,) = result["comparisons"]
    assert cmp["baseline"] == "reversed" and cmp["arm"] == "perfect"
    ap = cmp["metrics"]["ap"]
    assert ap["mean"] > 0.5 and ap["p_b_better"] >= 0.95 and ap["ci"][0] > 0


def test_the_baseline_defaults_to_the_first_arm_and_must_exist(world):
    assert run(world, arm_names=("reversed", "perfect"))["comparisons"][0]["baseline"] == "reversed"
    with pytest.raises(BenchmarkError, match="baseline"):
        run(world, baseline="nope")


# ---------- strata ----------


def test_title_only_strata_expose_an_arm_that_penalises_items_without_text(world):
    result = run(world, arm_names=("penalising", "perfect"))
    title_only = result["strata"]["title_only"]
    assert title_only["title-only"]["n"] == 10 and title_only["has text"]["n"] == 30
    assert title_only["title-only"]["show"] == 2 and title_only["has text"]["show"] == 6
    assert title_only["title-only"]["arms"]["penalising"]["recall@16"] == 0.0  # both title-only SHOW items missed
    assert title_only["has text"]["arms"]["penalising"]["recall@16"] == 1.0
    assert title_only["title-only"]["arms"]["perfect"]["recall@16"] == 1.0


def test_strata_partition_the_items_for_every_dimension(world):
    strata = run(world)["strata"]
    for dimension in ("title_only", "source", "category"):
        assert sum(cell["n"] for cell in strata[dimension].values()) == N, dimension
    assert set(strata["source"]) == {"hn", "arxiv", "hf_papers", "github"}


def test_small_strata_are_flagged_as_too_small_to_interpret(world):
    cell = run(world)["strata"]["title_only"]["title-only"]
    assert cell["show"] == 2 and cell["too_few"] is True
    assert run(world)["strata"]["title_only"]["has text"]["too_few"] is False  # 6 SHOW items is still small, but past the bar


def test_the_wildcard_stratum_uses_jevs_own_wildcard_answer(tmp_path):
    shot = make_snapshot(12)
    profile_path = tmp_path / "p.toml"
    profile_path.write_text(PROFILE_TOML, encoding="utf-8")
    profile = load_profile(profile_path)
    arm = arms.run_jev_arm(shot, profile, FakeJev(lambda t, n: dict(wildcard=0.9 if n % 3 == 0 else 0.1)), tmp_path, name="jev", now=NOW, workers=1, code=CODE)
    labels = make_labels(n=12)
    result = an.analyze(shot, labels, {"jev": arm}, config=CONFIG, now=NOW, code=CODE)
    cells = result["strata"]["wildcard_candidate"]
    assert cells["wildcard candidate"]["n"] == 4 and cells["not a wildcard"]["n"] == 8


def test_without_a_jev_arm_there_is_no_wildcard_stratum(world):
    llm_arms = {"a": make_arm(world["shot"], lambda n, i: record("ai", 1 + n % 5), name="a", kind="llm")}
    result = an.analyze(world["shot"], make_labels(), llm_arms, config=CONFIG, now=NOW, code=CODE)
    assert "wildcard_candidate" not in result["strata"]


# ---------- false negatives and false positives ----------


def test_false_negatives_list_each_missed_show_item_with_why_it_was_missed(world):
    fn = run(world, arm_names=("penalising",), config=an.Config(n_boot=0, fn_k=16))["false_negatives"]["penalising"]
    rows = {r["item_id"]: r for r in fn["rows"]}
    assert set(rows) == {500, 504}  # the two title-only SHOW items (their order is decided by recency: they tie on score)
    assert all(r["title_only"] and r["why"] == "low model score" for r in rows.values())
    assert fn["n_show"] == 8 and fn["n_missed"] == 2 and fn["by_reason"] == {"low model score": 2}
    assert rows[500]["title"] == "Item title 0" and all(r["rank"] > 16 for r in rows.values())
    assert [r["rank"] for r in fn["rows"]] == sorted(r["rank"] for r in fn["rows"])  # listed best-ranked miss first


def test_a_perfect_arm_has_no_false_negatives(world):
    assert run(world, arm_names=("perfect",))["false_negatives"]["perfect"]["rows"] == []


def test_the_reason_distinguishes_filtered_out_pushed_down_by_popularity_and_low_score():
    shot = make_snapshot(8)
    for item in shot.items:
        item["source"] = "hn"
        item["published_at"] = "2026-09-18T00:00:00+00:00"
    for i, item in enumerate(shot.items):
        item["signals"] = {"hn": {"points": 10 * (8 - i) if i else 1}}  # item 0 is the LEAST popular of eight equals
    per = {7: record("other", 5)}  # a SHOW item the curator can never show
    arm = make_arm(shot, lambda n, item: per.get(n, record("ai", 3)), name="x", kind="llm")
    labels = LabelSet({500: 2, 507: 2, **{500 + i: 0 for i in range(1, 7)}}, 8, "frozen", "h", {"SHOW": 2, "MAYBE": 0, "SKIP": 6})
    fn = an.analyze(shot, labels, {"x": arm}, config=an.Config(n_boot=0, ks=(3,), fn_k=3), now=NOW, code=CODE)["false_negatives"]["x"]
    reasons = {r["item_id"]: r["why"] for r in fn["rows"]}
    assert reasons[507] == "never shown: category is 'other'"
    assert reasons[500] == "popularity or cross-source boost pushed it below the cut"  # equal on the model's score, last on popularity


def test_false_positives_are_skip_items_inside_the_top_k(world):
    fp = run(world, arm_names=("reversed",), config=an.Config(n_boot=0, fn_k=5))["false_positives"]["reversed"]
    assert fp["k"] == 5 and fp["n_in_top_k"] == 5 and len(fp["rows"]) == 5 and all(r["label"] == "SKIP" for r in fp["rows"])


def test_jev_features_are_attached_to_misses_so_the_cause_can_be_read(tmp_path):
    shot = make_snapshot(12)
    profile_path = tmp_path / "p.toml"
    profile_path.write_text(PROFILE_TOML, encoding="utf-8")
    arm = arms.run_jev_arm(shot, load_profile(profile_path), FakeJev(lambda t, n: dict(match=0, substance=0)), tmp_path, name="jev", now=NOW, workers=1, code=CODE)
    fn = an.analyze(shot, make_labels(n=12), {"jev": arm}, config=an.Config(n_boot=0, fn_k=2), now=NOW, code=CODE)["false_negatives"]["jev"]
    assert {"interest_match", "substance", "wildcard", "buildable", "low_value", "too_little_info"} <= set(fn["rows"][0]["features"])


# ---------- disagreements ----------


def test_disagreements_list_the_items_the_two_arms_rank_furthest_apart(world):
    result = run(world, baseline="perfect", config=an.Config(n_boot=0, top_disagreements=3))
    (d,) = result["disagreements"]
    assert d["baseline"] == "perfect" and d["arm"] == "reversed"
    assert d["spearman"] == pytest.approx(-1.0) and d["top_k_overlap"] == 0.0
    top = d["baseline_higher"][0]  # baseline ranks it far higher than the other arm does
    assert top["item_id"] == 500 and top["baseline_rank"] == 1 and top["arm_rank"] == N and top["label"] == "SHOW"
    assert len(d["baseline_higher"]) == len(d["arm_higher"]) == 3


# ---------- honesty about incomplete inputs ----------


def test_partial_labels_give_a_preliminary_analysis_that_says_so(world):
    labels = make_labels(only=range(0, N, 2), source="live")
    result = run(world, labels=labels)
    meta = result["meta"]
    assert meta["preliminary"] is True and meta["n_labelled"] == 20 and meta["n_items"] == N
    assert meta["effective_ks"] == {"4": 2, "10": 5, "16": 8, "32": 16}
    assert result["arms"]["perfect"]["metrics"]["auc_show_vs_skip"]["value"] == pytest.approx(1.0)


def test_complete_frozen_labels_are_not_preliminary(world):
    assert run(world)["meta"]["preliminary"] is False
    assert run(world, labels=make_labels(source="live"))["meta"]["preliminary"] is True  # live labels can still change


def test_an_arm_that_missed_some_items_reports_its_coverage_and_puts_them_last(world):
    arm = world["arms"]["perfect"]
    partial = arms.Arm("gappy", "jev", dict(arm.meta), {i: r for i, r in arm.records.items() if i != 500})
    result = an.analyze(world["shot"], make_labels(), {"gappy": partial}, config=an.Config(n_boot=0), now=NOW, code=CODE)
    coverage = result["arms"]["gappy"]["coverage"]
    assert coverage["scored"] == 39 and coverage["unscored"] == 1
    assert result["arms"]["gappy"]["metrics"]["recall@16"]["value"] == pytest.approx(7 / 8)


def test_an_arm_from_a_different_snapshot_is_refused(world):
    bad = make_arm(world["shot"], lambda n, i: record(), name="bad")
    bad.meta["snapshot_id"] = "somethingelse"
    with pytest.raises(BenchmarkError, match="different snapshot"):
        an.analyze(world["shot"], make_labels(), {"bad": bad}, config=CONFIG, now=NOW, code=CODE)


def test_labels_for_items_that_are_not_in_the_snapshot_are_refused(world):
    labels = make_labels()
    labels.grades[99999] = 2
    with pytest.raises(BenchmarkError, match="not in the snapshot"):
        run(world, labels=labels)


def test_no_arms_is_an_error(world):
    with pytest.raises(BenchmarkError, match="at least one arm"):
        an.analyze(world["shot"], make_labels(), {}, config=CONFIG, now=NOW, code=CODE)


# ---------- the yardstick stays fixed while the formula changes ----------


def test_a_changed_formula_is_compared_against_the_same_frozen_labels(tmp_path):
    shot = make_snapshot(N)
    profile_path = tmp_path / "p.toml"
    profile_path.write_text(PROFILE_TOML, encoding="utf-8")
    ordered = lambda title, n: dict(match=3 if int(title.split()[-1]) < 8 else 0, substance=3 if int(title.split()[-1]) < 8 else 0)  # noqa: E731
    current = arms.run_jev_arm(shot, load_profile(profile_path), FakeJev(ordered), tmp_path, name="current", now=NOW, workers=1, code=CODE)

    def broken_formula(answers):
        d = jt.derive(answers)
        return jt.Derived(1 - d.relevance, 1 + round(4 * (1 - d.relevance)), d.category, d.features, d.flags)

    changed = arms.rescore_jev_arm(current, "changed", derive_fn=broken_formula, code=CODE)
    labels = make_labels()
    result = an.analyze(shot, labels, {"current": current, "changed": changed}, config=CONFIG, now=NOW, code=CODE)
    assert result["meta"]["labels_hash"] == "hash123"  # both scored against the identical frozen labels
    assert result["arms"]["current"]["metrics"]["auc_show_vs_skip"]["value"] > result["arms"]["changed"]["metrics"]["auc_show_vs_skip"]["value"]
    assert result["comparisons"][0]["metrics"]["auc_show_vs_skip"]["mean"] < 0  # the change made things worse, and the report says so


# ---------- the report ----------


def test_the_result_is_plain_json(world):
    json.dumps(run(world))


def test_the_markdown_report_has_every_section_and_the_caveats(world):
    text = an.render_markdown(run(world, arm_names=("perfect", "penalising", "reversed")))
    for heading in ("Headline metrics", "Comparison with the baseline", "Performance by group", "Missed SHOW items", "Top disagreements", "Label reliability"):
        assert heading in text, heading
    assert "recall@16" in text and "perfect" in text and "hash123" in text
    assert "PRELIMINARY" not in text


def test_the_markdown_report_carries_a_loud_banner_when_preliminary(world):
    text = an.render_markdown(run(world, labels=make_labels(only=range(0, N, 2), source="live")))
    assert "PRELIMINARY" in text.splitlines()[0] or "PRELIMINARY" in text.splitlines()[2]
    assert "20 of 40" in text


def test_undefined_numbers_are_shown_as_na_never_as_zero(world):
    labels = LabelSet({500 + i: 0 for i in range(N)}, N, "frozen", "h", {"SHOW": 0, "MAYBE": 0, "SKIP": N})  # nothing to find
    text = an.render_markdown(run(world, labels=labels))
    assert "n/a" in text
    assert an.fmt(None) == "n/a" and an.fmt(0.5) == "0.50"


def test_the_repeat_label_reliability_is_reported(world):
    labels = make_labels()
    labels.retest = [{"item_id": 500, "first": "SHOW", "repeat": "SHOW"}, {"item_id": 501, "first": "SHOW", "repeat": "SKIP"}]
    result = run(world, labels=labels)
    assert result["labels"]["retest"]["n"] == 2 and result["labels"]["retest"]["show_skip_flips"] == 1
    assert "1 of 2" in an.render_markdown(result) or "50%" in an.render_markdown(result)


# ---------- protecting the labels that are still to be made ----------


def test_while_labelling_is_unfinished_no_item_level_detail_is_produced(world):
    """A preliminary report that named items with their model scores would let the reader's remaining labels be
    influenced by the models, defeating the blind labelling. Aggregate numbers are fine; item lists are withheld."""
    result = run(world, labels=make_labels(only=range(0, N, 2), source="live"))
    assert result["meta"]["items_hidden"] is True
    assert all(fn["rows"] == [] for fn in result["false_negatives"].values())
    assert all(fp["rows"] == [] for fp in result["false_positives"].values())
    assert all(d["baseline_higher"] == [] and d["arm_higher"] == [] for d in result["disagreements"])
    assert result["false_negatives"]["perfect"]["n_missed"] is None  # even the count of misses could hint at which items
    text = an.render_markdown(result)
    assert "Item title" not in text and "withheld" in text.lower()


def test_once_every_item_is_labelled_the_item_lists_appear_even_before_freezing(world):
    result = run(world, labels=make_labels(source="live"))
    assert result["meta"]["items_hidden"] is False and result["false_negatives"]["reversed"]["rows"]
