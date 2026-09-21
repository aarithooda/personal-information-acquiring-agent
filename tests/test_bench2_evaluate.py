"""Benchmark v2 evaluation: implements METHODOLOGY.md sections 7 to 14, checked on synthetic data whose right answers are known."""

import json
import math
from datetime import datetime, timezone

import pytest
from fakes import SmartLLM
from jevfakes import V2_PROFILE_TOML, SyntheticJev
from test_bench_arms import CODE, make_arm, make_snapshot, record

from benchmark_v2 import common as c
from benchmark_v2 import evaluate as ev
from benchmarks.arms import Arm
from benchmarks.common import BenchmarkError

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


# ---------- the three primary metrics, as METHODOLOGY section 8 defines them ----------


def test_the_primary_metrics_are_graded_ndcg_at_16_lenient_ap_and_lenient_auc():
    assert ev.PRIMARY == {"P1": "ndcg@16", "P2": "ap_lenient", "P3": "auc_lenient"}


def test_lenient_auc_is_the_chance_a_show_or_maybe_outranks_a_skip():
    # ranking [MAYBE, SKIP, SHOW, SKIP]: positives at ranks 1 and 3; skips at ranks 2 and 4
    assert ev.auc_lenient([1, 0, 2, 0]) == pytest.approx((2 + 1) / 4)  # pos1 beats both skips, pos3 beats one
    assert ev.auc_lenient([2, 1, 0, 0]) == 1.0 and ev.auc_lenient([0, 0, 1, 2]) == 0.0
    assert ev.auc_lenient([1, 1, 2]) is None and ev.auc_lenient([0, 0]) is None  # undefined, never zero


def test_maybe_counts_as_relevant_in_lenient_ap_and_as_gain_one_in_ndcg():
    m = ev.full_metrics([1, 0, 2, 0, 0])
    assert m["ap_lenient"] == pytest.approx((1 / 1 + 2 / 3) / 2)
    dcg = 1 / math.log2(2) + 2 / math.log2(4)
    ideal = 2 / math.log2(2) + 1 / math.log2(3)
    assert m["ndcg@16"] == pytest.approx(dcg / ideal)
    assert m["auc_lenient"] == pytest.approx(ev.auc_lenient([1, 0, 2, 0, 0]))


def test_lenient_capture_is_hits_over_the_best_possible_at_k():
    assert ev.full_metrics([1, 0, 2, 0, 0], ks=(1, 3))["capture_lenient@1"] == 1.0
    assert ev.full_metrics([0, 1, 2, 0, 0], ks=(1,))["capture_lenient@1"] == 0.0


def test_the_full_metric_set_carries_every_v1_metric_family_too():
    m = ev.full_metrics([2, 1, 0, 0, 1, 0])
    for key in ("precision@4", "recall@10", "capture@16", "ndcg@32", "precision_lenient@4", "recall_lenient@32", "ap", "auc_show_vs_skip", "spearman"):
        assert key in m


# ---------- the model-free reference orderings ----------


def ref_items():
    def it(i, published, text, signals):
        return {"id": i, "source": "hn", "external_id": str(i), "canonical_url": f"https://x/{i}", "url": f"https://x/{i}", "title": f"T{i}", "content_raw": text, "published_at": published, "discovered_at": "2026-09-20T00:00:00+00:00", "signals": signals}

    return [
        it(1, "2026-08-01T00:00:00+00:00", None, {"hn": {"points": 500}}),
        it(2, "2026-08-03T00:00:00+00:00", "text", {"hn": {"points": 100}}),
        it(3, "2026-08-02T00:00:00+00:00", "text", {"hn": {"points": 300}}),
        it(4, "2026-08-04T00:00:00+00:00", None, {"arxiv": {"categories": ["cs.AI"]}}),
    ]


def test_r1_is_newest_first():
    assert ev.reference_orderings(ref_items())["R1-newest"].ranked == [4, 2, 3, 1]


def test_r2_puts_items_with_text_first_then_newest():
    assert ev.reference_orderings(ref_items())["R2-text-first"].ranked == [2, 3, 4, 1]


def test_r3_ranks_by_within_source_popularity_percentile_with_newest_breaking_ties():
    order = ev.reference_orderings(ref_items())["R3-popularity"].ranked
    assert order[:3] == [1, 3, 2]  # 500 > 300 > 100 points; the item with no popularity metric is last
    assert order[3] == 4


# ---------- uncertainty ----------


def small_world(n=40):
    ids = list(range(n))
    grade = {i: (2 if i < 4 else 1 if i < 12 else 0) for i in ids}
    good = {i: i for i in ids}
    bad = {i: n - 1 - i for i in ids}
    return ids, grade, {"good": good, "bad": bad}


def test_the_bootstrap_is_reproducible_and_uses_the_same_resamples_for_every_arm():
    ids, grade, pos = small_world()
    a = ev.bootstrap_all(ids, grade, pos, n=50, seed=7)
    assert a == ev.bootstrap_all(ids, grade, pos, n=50, seed=7)
    assert len(a["good"]["auc_lenient"]) == len(a["bad"]["auc_lenient"]) == 50
    for g, b in zip(a["good"]["auc_lenient"], a["bad"]["auc_lenient"]):
        if g is not None:
            assert g + b == pytest.approx(1.0)  # mirror-image rankings on identical resamples


def test_the_permutation_test_is_one_sided_and_conservative():
    ids, grade, pos = small_world()
    p = ev.permutation_pvalues(ids, grade, pos, n=400, seed=3)
    assert p["good"]["P3"]["observed"] == 1.0 and p["good"]["P3"]["p"] < 0.05
    assert p["bad"]["P3"]["p"] > 0.9  # a ranking that is worse than chance is nowhere near "skilled"
    assert p["good"]["P2"]["p"] >= 1 / 401  # never exactly 0: (1 + hits) / (1 + shuffles)
    assert p == ev.permutation_pvalues(ids, grade, pos, n=400, seed=3)


# ---------- label reliability on the repeats ----------


def test_repeat_reliability_compares_the_v2_label_with_the_v1_label():
    v2 = {1: 2, 2: 1, 3: 0, 4: 0, 5: 0, 6: 1}
    v1 = {10: 2, 20: 0, 30: 0, 40: 1, 50: 0, 60: 1}
    r = ev.reliability(v2, v1, {1: 10, 2: 20, 3: 30, 4: 40, 5: 50, 6: 60})
    assert r["n"] == 6 and r["exact_agreement"] == pytest.approx(4 / 6)
    assert r["confusion"]["SKIP"] == {"SKIP": 2, "MAYBE": 1, "SHOW": 0} and r["confusion"]["MAYBE"]["SKIP"] == 1
    assert r["show_skip_flips"] == 0 and r["positive_rate_v1"] == pytest.approx(3 / 6) and r["positive_rate_v2"] == pytest.approx(3 / 6)
    assert r["kappa_below_floor"] == (r["weighted_kappa"] < c.KAPPA_FLOOR)


def test_the_noise_reference_uses_the_v1_label_as_a_ranker_of_the_v2_label_with_ties_worth_half():
    r = ev.reliability({1: 1, 2: 0}, {10: 1, 20: 0}, {1: 10, 2: 20})
    assert r["noise_reference_auc_lenient"] == 1.0
    r = ev.reliability({1: 1, 2: 0}, {10: 0, 20: 0}, {1: 10, 2: 20})
    assert r["noise_reference_auc_lenient"] == 0.5


# ---------- title-only analysis ----------


def test_the_representation_ratio_flags_an_arm_whose_top_32_ignores_title_only_positives():
    ids = list(range(64))
    title_only = {i for i in ids if i % 2 == 0}
    grade = {i: 1 if i < 16 else 0 for i in ids}  # positives 0..15: half are title-only
    text_first = {i: (0 if i not in title_only else 100) + i for i in ids}  # every text item ahead of every title-only one
    fair = {i: i for i in ids}
    t = ev.title_only_analysis(ids, grade, {"texty": text_first, "fair": fair}, title_only)
    assert t["positive_rate"]["title_only"] == pytest.approx(8 / 32) and t["positive_rate"]["has_text"] == pytest.approx(8 / 32)
    assert t["arms"]["texty"]["top32_title_only_share"] == 0.0 and t["arms"]["texty"]["representation_ratio"] == 0.0
    assert t["arms"]["texty"]["under_represented"] is True and t["arms"]["fair"]["under_represented"] is False
    assert t["arms"]["fair"]["representation_ratio"] == pytest.approx(1.0)
    assert set(t["arms"]["fair"]) >= {"top16_title_only_share", "top32_title_only_share", "auc_lenient_title_only", "auc_lenient_has_text"}


# ---------- the decision rules, section 14 ----------


def prim(p1, p2, p3):
    return {"P1": p1, "P2": p2, "P3": p3}


def test_d1_needs_both_permutation_p_values_below_0_025():
    v = ev.verdicts(perm={"P2": {"p": 0.01}, "P3": {"p": 0.02}}, comparisons={}, kappa=0.6, n_positive=30)
    assert v["D1"] == "Ranking skill demonstrated"
    v = ev.verdicts(perm={"P2": {"p": 0.01}, "P3": {"p": 0.03}}, comparisons={}, kappa=0.6, n_positive=30)
    assert v["D1"] == "Ranking skill not demonstrated"


def cmp(ap_ci, ap_mean, ndcg_mean):
    return {"P2": {"ci": ap_ci, "mean": ap_mean}, "P1": {"ci": (ndcg_mean - 0.1, ndcg_mean + 0.1), "mean": ndcg_mean}}


def test_d2_outperforms_only_when_the_ap_interval_is_above_zero_and_ndcg_points_the_same_way():
    ok = ev.verdicts(perm={"P2": {"p": 0.5}, "P3": {"p": 0.5}}, comparisons={"X": cmp((0.01, 0.2), 0.1, 0.05)}, kappa=0.6, n_positive=30)
    assert ok["D2"]["X"] == "A outperforms X"
    assert ev.verdicts(perm={"P2": {"p": 0.5}, "P3": {"p": 0.5}}, comparisons={"X": cmp((0.01, 0.2), 0.1, -0.05)}, kappa=0.6, n_positive=30)["D2"]["X"] == "cannot tell"
    assert ev.verdicts(perm={"P2": {"p": 0.5}, "P3": {"p": 0.5}}, comparisons={"X": cmp((-0.05, 0.2), 0.1, 0.05)}, kappa=0.6, n_positive=30)["D2"]["X"] == "cannot tell"
    assert ev.verdicts(perm={"P2": {"p": 0.5}, "P3": {"p": 0.5}}, comparisons={"X": cmp((-0.3, -0.01), -0.1, -0.05)}, kappa=0.6, n_positive=30)["D2"]["X"] == "A underperforms X"


def test_d3_prefixes_every_verdict_when_labels_are_noisy():
    v = ev.verdicts(perm={"P2": {"p": 0.01}, "P3": {"p": 0.01}}, comparisons={"X": cmp((0.01, 0.2), 0.1, 0.05)}, kappa=0.3, n_positive=30)
    assert v["D1"].startswith("Limited by label noise: ") and v["D2"]["X"].startswith("Limited by label noise: ")


def test_d4_withholds_every_verdict_when_positives_are_too_few_or_too_many():
    for n in (19, 141):
        v = ev.verdicts(perm={"P2": {"p": 0.001}, "P3": {"p": 0.001}}, comparisons={"X": cmp((0.01, 0.2), 0.1, 0.05)}, kappa=0.9, n_positive=n)
        assert v["D1"].startswith("Underpowered") and v["D2"]["X"].startswith("Underpowered")
    assert not ev.verdicts(perm={"P2": {"p": 0.001}, "P3": {"p": 0.001}}, comparisons={}, kappa=0.9, n_positive=20)["D1"].startswith("Underpowered")


# ---------- a whole synthetic evaluation ----------


def synthetic_corpus(tmp_path, n_new=60, n_rep=10):
    """A tiny Benchmark v2 data dir: items, manifest, frozen labels, and v1 labels for the repeats."""
    from benchmark_v2.corpus import compute_corpus_id
    from benchmarks.common import write_json_atomic

    shot = make_snapshot(n_new + n_rep)
    items = shot.items
    for k, item in enumerate(items):
        item["id"] = k + 1
        item["title"] = f"SECRET TITLE {k + 1}"
    ids = [i["id"] for i in items]
    new_ids, rep_ids = ids[:n_new], ids[n_new:]
    grade_of = lambda i: 2 if i <= 6 else 1 if i <= 20 else 0  # noqa: E731
    data = tmp_path / "data"
    write_json_atomic(data / c.ITEMS_FILE, {"format": 1, "snapshot_id": compute_corpus_id(items), "exported_at": "x", "source_schema": 0, "items": items})
    manifest = {"origin_by_id": {**{str(i): "new" for i in new_ids}, **{str(i): "repeat" for i in rep_ids}}, "new": [{"v2_id": i} for i in new_ids], "repeats": [{"v2_id": i, "v1_item_id": 1000 + i} for i in rep_ids]}
    write_json_atomic(data / c.MANIFEST_FILE, manifest)
    names = {2: "SHOW", 1: "MAYBE", 0: "SKIP"}
    write_json_atomic(data / "labels_frozen.json", {"snapshot_id": compute_corpus_id(items), "labels_hash": "hh", "labels": [{"item_id": i, "label": names[grade_of(i)], "canonical_url": items[i - 1]["canonical_url"], "fold": 0} for i in ids], "retest": []})
    v1 = {1000 + i: grade_of(i) for i in rep_ids}
    return data, shot, v1


def good_arm(shot, name, kind="jev", flip=False):
    from test_bench_arms import record as rec

    def rel(n, item):
        k = item["id"]
        s = 1.0 - k / 100
        return (1 - s) if flip else s

    return make_arm(shot, lambda n, item: rec("ai", 3, relevance=rel(n, item)), name=name, kind=kind)


def test_a_full_evaluation_reports_every_pre_declared_section_and_never_names_an_item(tmp_path):
    data, shot, v1 = synthetic_corpus(tmp_path)
    ctx = ev.load_context(data, v1_grades=v1)
    arms = {"jev-v2": good_arm(shot, "jev-v2"), "llm-stage1": good_arm(shot, "llm-stage1", kind="llm", flip=True)}
    result = ev.analyze(ctx, arms, now=NOW, code=CODE, resamples=200, shuffles=200)
    text = ev.render(result)
    for section in ("Labels", "Primary metrics", "Above-chance test", "Comparisons", "Repeat-label reliability", "Title-only analysis", "Stratification", "Secondary metrics", "Verdicts", "Provenance"):
        assert section in text, section
    assert "SECRET TITLE" not in text and "https://" not in text  # aggregate patterns only, no item-level rationalisation
    json.dumps(result)  # plain JSON


def test_the_primary_ranking_pool_is_the_new_items_only_and_the_repeats_are_kept_out_of_p1_to_p3(tmp_path):
    data, shot, v1 = synthetic_corpus(tmp_path)
    ctx = ev.load_context(data, v1_grades=v1)
    assert len(ctx.new_items) == 60 and len(ctx.items) == 70
    result = ev.analyze(ctx, {"jev-v2": good_arm(shot, "jev-v2"), "llm-stage1": good_arm(shot, "llm-stage1", kind="llm", flip=True)}, now=NOW, code=CODE, resamples=100, shuffles=100)
    assert result["labels"]["new"] == {"SHOW": 6, "MAYBE": 14, "SKIP": 40} and result["meta"]["primary_pool"] == 60
    assert result["primary"]["jev-v2"]["P3"]["value"] == 1.0  # ranked among the 60 new items only
    assert result["all_items"]["jev-v2"]["P3"] is not None  # the secondary 70-item ranking exists and is labelled as such


def test_the_decision_comparators_are_the_llm_stage_1_and_the_text_first_reference(tmp_path):
    data, shot, v1 = synthetic_corpus(tmp_path)
    ctx = ev.load_context(data, v1_grades=v1)
    result = ev.analyze(ctx, {"jev-v2": good_arm(shot, "jev-v2"), "llm-stage1": good_arm(shot, "llm-stage1", kind="llm", flip=True)}, now=NOW, code=CODE, resamples=200, shuffles=100)
    assert set(result["verdicts"]["D2"]) == {"llm-stage1", "R2-text-first"}
    assert set(result["comparisons"]) >= {"llm-stage1", "R1-newest", "R2-text-first", "R3-popularity"}
    assert result["verdicts"]["D2"]["llm-stage1"] == "A outperforms llm-stage1"  # a perfect ranking against a reversed one


def test_the_evaluation_needs_the_two_decision_arms(tmp_path):
    data, shot, v1 = synthetic_corpus(tmp_path)
    with pytest.raises(BenchmarkError, match="jev-v2"):
        ev.analyze(ev.load_context(data, v1_grades=v1), {"llm-stage1": good_arm(shot, "llm-stage1", kind="llm")}, now=NOW, code=CODE, resamples=10, shuffles=10)


# ---------- running the arms (frozen inputs, one run, full provenance) ----------


def frozen_ctx(tmp_path):
    data, shot, v1 = synthetic_corpus(tmp_path)
    (data / c.FREEZE_FILE).write_text(json.dumps({"corpus_id": "x", "production": {"profile_hash": None}}), encoding="utf-8")
    return data, shot


def test_the_jev_v2_arm_runs_the_current_design_once_and_records_full_provenance(tmp_path):
    from pia.profile import load_profile

    data, shot = frozen_ctx(tmp_path)
    ctx = ev.load_context(data, v1_grades={})
    prof = tmp_path / "p.toml"
    prof.write_text(V2_PROFILE_TOML, encoding="utf-8")
    jev = SyntheticJev()
    arm = ev.run_jev_v2(ctx, load_profile(prof), jev, data / "scratch", model="jev-1.13.0", now=NOW, workers=1, code=CODE)
    assert arm.name == "jev-v2" and arm.kind == "jev" and len(arm.records) == 70 and arm.meta["complete"] is True
    m = arm.meta
    assert m["question_set"] == "jev-triage-v2" and m["benchmark_version"] == "v2" and m["jev_model_requested"] == "jev-1.13.0" and m["models"] == ["jev-1.13.0"]
    assert m["profile_hash"] == load_profile(prof).hash and m["derivation"] == "jev-triage-v2" and len(m["ranking_version"]) == 64
    assert m["code"] == {"commit": "abc1234", "dirty": False} and m["input_tokens"] == 70 * 4 * 1000
    assert len(jev.calls) == 70 * 4  # four requests per item, each item asked about once


def test_the_llm_arm_is_the_production_stage_1_with_provenance(tmp_path):
    data, shot = frozen_ctx(tmp_path)
    ctx = ev.load_context(data, v1_grades={})
    arm = ev.run_llm(ctx, SmartLLM(), data / "scratch", now=NOW, code=CODE)
    assert arm.name == "llm-stage1" and arm.kind == "llm" and len(arm.records) == 70 and arm.meta["benchmark_version"] == "v2"
    assert arm.meta["models"] == ["openai/gpt-oss-20b"] and arm.meta["prompt_version"] == "stage1-v2" and len(arm.meta["ranking_version"]) == 64


def test_an_arm_may_only_be_saved_once_and_only_when_complete(tmp_path):
    from benchmarks.arms import load_arm

    data, shot = frozen_ctx(tmp_path)
    ctx = ev.load_context(data, v1_grades={})
    arm = ev.run_llm(ctx, SmartLLM(), data / "scratch", now=NOW, code=CODE)
    ev.save_once(arm, data)
    assert load_arm(data / "arms" / "llm-stage1.json").name == "llm-stage1"
    with pytest.raises(BenchmarkError, match="already exists"):
        ev.save_once(arm, data)
    partial = Arm("jev-v2", "jev", {"complete": False, "n_scored": 3, "n_items": 70}, {})
    with pytest.raises(BenchmarkError, match="3 of 70"):
        ev.save_once(partial, data)


def test_the_ranking_version_changes_when_the_ranking_code_changes(tmp_path):
    a = ev.ranking_version()
    assert len(a) == 64 and a == ev.ranking_version()


# ---------- the gates ----------


def test_evaluation_refuses_to_start_unless_the_labels_are_frozen_and_the_freeze_is_intact(tmp_path, monkeypatch):
    data, shot, v1 = synthetic_corpus(tmp_path)
    (data / "labels_frozen.json").unlink()
    monkeypatch.setattr(ev.fz, "verify", lambda *a, **k: [])
    with pytest.raises(BenchmarkError, match="labels are not frozen"):
        ev.gate(data)
    (data / "labels_frozen.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ev.fz, "verify", lambda *a, **k: ["production code differs"])
    with pytest.raises(BenchmarkError, match="production code differs"):
        ev.gate(data)
    monkeypatch.setattr(ev.fz, "verify", lambda *a, **k: [])
    ev.gate(data)


def test_the_evaluation_module_never_reads_v1_labels_while_building_or_running_arms():
    import ast
    import pathlib

    text = pathlib.Path(ev.__file__).read_text(encoding="utf-8")
    assert "def load_v1_grades" in text  # the ONE place v1 labels are read, and only for the repeat-reliability analysis (methodology section 10)
    tree = ast.parse(text)
    readers = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and "labels_frozen" in ast.get_source_segment(text, n)]
    assert readers and set(readers) <= {"load_v1_grades", "load_context", "_v2_labels"}


def test_an_undefined_metric_yields_no_skill_claim_instead_of_an_error():
    v = ev.verdicts(perm={"P2": {"p": None}, "P3": {"p": 0.01}}, comparisons={"X": {"P2": {"ci": (None, None), "mean": None}, "P1": {"ci": (None, None), "mean": None}}}, kappa=0.6, n_positive=30)
    assert v["D1"] == "Ranking skill not demonstrated" and v["D2"]["X"] == "cannot tell"


# ---------- the command line ----------


def test_the_command_line_exposes_the_documented_arguments_and_refuses_before_the_labels_are_frozen(tmp_path):
    from typer.testing import CliRunner

    runner = CliRunner()
    help_text = runner.invoke(ev.app, ["arm", "--help"]).output
    assert "which" in help_text and "--data-dir" in help_text  # the wrapper must not hide the real signature
    bad = runner.invoke(ev.app, ["arm", "bogus", "--data-dir", str(tmp_path)])
    assert bad.exit_code != 0 and "must be one of" in bad.output
    early = runner.invoke(ev.app, ["arm", "jev-v2", "--data-dir", str(tmp_path)])
    assert early.exit_code == 1 and "labels are not frozen" in early.output and "Traceback" not in early.output
    assert runner.invoke(ev.app, ["analyze", "--data-dir", str(tmp_path)]).exit_code == 1
