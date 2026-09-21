"""Arms: a "system under test" is its raw Stage 1 output per item, ranked by the PRODUCTION ranking code."""

from datetime import datetime, timedelta, timezone

import pytest
from fakes import SmartLLM
from test_jev_triage import PROFILE_TOML, FakeJev

from benchmarks import arms
from benchmarks.common import BenchmarkError
from benchmarks.snapshot import Snapshot, compute_snapshot_id
from pia.briefing.budget import headline_budget, also_budget
from pia.briefing.curate import make_curator
from pia.jev import triage as jt
from pia.llm.client import LLMUnavailable
from pia.llm.prompts import PROMPT_VERSION, STAGE1_MODEL
from pia.profile import load_profile

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
CODE = lambda: {"commit": "abc1234", "dirty": False}  # noqa: E731


def make_snapshot(n=12, signals=None):
    sources = ["hn", "arxiv", "hf_papers", "github"]
    items = []
    for i in range(n):
        source = sources[i % 4]
        items.append(
            {
                "id": 500 + i,
                "source": source,
                "external_id": str(i),
                "canonical_url": f"https://example.com/{i}",
                "url": f"https://example.com/{i}",
                "title": f"Item title {i}",
                "content_raw": None if source == "hn" else f"Some text about item {i}.",
                "published_at": f"2026-09-{10 + i % 9:02d}T00:00:00+00:00",
                "discovered_at": "2026-09-20T00:00:00+00:00",
                "signals": (signals or {}).get(i, {source: {}}),
            }
        )
    return Snapshot(items, compute_snapshot_id(items), NOW.isoformat())


def record(category="ai", importance=3, relevance=None, summary="s"):
    return {
        "model": "test-model",
        "prompt_version": "pv",
        "category": category,
        "importance": importance,
        "summary": summary,
        "relevance": relevance,
        "details": None,
        "profile_hash": None,
    }


def make_arm(shot, per_item, name="a", kind="llm"):
    return arms.Arm(name, kind, {"snapshot_id": shot.snapshot_id}, {item["id"]: per_item(n, item) for n, item in enumerate(shot.items)})


@pytest.fixture
def profile(tmp_path):
    path = tmp_path / "interests.toml"
    path.write_text(PROFILE_TOML, encoding="utf-8")
    return load_profile(path)


# ---------- the ephemeral database the arms run in ----------


def test_the_scratch_database_holds_the_frozen_items_with_their_original_ids_and_nothing_else(tmp_path):
    shot = make_snapshot()
    conn = arms.build_scratch_db(shot, tmp_path / "s.db")
    rows = conn.execute("SELECT * FROM items ORDER BY id").fetchall()
    assert [r["id"] for r in rows] == [i["id"] for i in shot.items]
    assert {r["status"] for r in rows} == {"discovered"} and all(r["briefing_id"] is None for r in rows)
    assert rows[0]["title"] == shot.items[0]["title"] and rows[1]["content_raw"] == shot.items[1]["content_raw"]
    assert conn.execute("SELECT COUNT(*) FROM enrichments").fetchone()[0] == 0


def test_reusing_a_scratch_database_from_another_snapshot_is_refused(tmp_path):
    arms.build_scratch_db(make_snapshot(12), tmp_path / "s.db").close()
    with pytest.raises(BenchmarkError, match="different snapshot"):
        arms.build_scratch_db(make_snapshot(13), tmp_path / "s.db")


# ---------- running the current production Stage 1 ----------


def test_the_jev_arm_runs_the_production_triager_and_keeps_raw_answers(tmp_path, profile):
    shot = make_snapshot()
    client = FakeJev(lambda title, n: dict(match=3, substance=2))
    arm = arms.run_jev_arm(shot, profile, client, tmp_path, name="jev", now=NOW, workers=1, code=CODE)
    assert arm.kind == "jev" and set(arm.records) == {i["id"] for i in shot.items}
    r = arm.records[500]
    assert r["details"]["answers"]["interest_match"]["score"] == 3.0  # RAW answers survive, so derive() can be re-run later
    assert r["relevance"] is not None and r["profile_hash"] == profile.hash and r["model"] == "jev-1.13.0"
    assert arm.meta["snapshot_id"] == shot.snapshot_id and arm.meta["complete"] is True
    assert arm.meta["profile_hash"] == profile.hash and arm.meta["question_set"] == jt.QUESTION_SET_VERSION
    assert arm.meta["code"] == {"commit": "abc1234", "dirty": False}
    assert arm.meta["input_tokens"] == 12 * 4400 and arm.meta["elapsed_seconds"] >= 0


def test_the_jev_arm_has_no_llm_fallback_so_it_measures_jev_alone(tmp_path, profile):
    shot = make_snapshot()

    def script(title, n):
        raise LLMUnavailable("down")

    arm = arms.run_jev_arm(shot, profile, FakeJev(script), tmp_path, name="jev", now=NOW, workers=1, code=CODE)
    assert arm.records == {} and arm.meta["complete"] is False  # nothing quietly scored by a different model


def test_an_interrupted_arm_resumes_without_paying_for_finished_items_again(tmp_path, profile):
    shot = make_snapshot(12)

    def dies_after_three(title, n):
        if n > 3:
            raise LLMUnavailable("down")
        return {}

    first = arms.run_jev_arm(shot, profile, FakeJev(dies_after_three), tmp_path, name="jev", now=NOW, workers=1, code=CODE)
    assert len(first.records) == 3 and first.meta["complete"] is False

    healthy = FakeJev()
    second = arms.run_jev_arm(shot, profile, healthy, tmp_path, name="jev", now=NOW, workers=1, code=CODE)
    assert len(healthy.calls) == 9, "the three finished items were requested again"
    assert len(second.records) == 12 and second.meta["complete"] is True


def test_the_llm_arm_runs_the_production_stage1_and_records_its_model_and_prompt(tmp_path):
    shot = make_snapshot()
    llm = SmartLLM(scores={"Item title 3": ("research", 5)})
    arm = arms.run_llm_arm(shot, llm, tmp_path, name="llm", now=NOW, code=CODE)
    assert arm.kind == "llm" and len(arm.records) == 12 and arm.meta["complete"] is True
    assert arm.records[503]["category"] == "research" and arm.records[503]["importance"] == 5
    assert arm.records[500]["relevance"] is None  # the LLM triage has no continuous score
    assert arm.meta["models"] == [STAGE1_MODEL] and arm.meta["prompt_version"] == PROMPT_VERSION


def test_arms_round_trip_through_a_file_and_are_frozen_once_written(tmp_path):
    shot = make_snapshot()
    arm = make_arm(shot, lambda n, item: record(importance=1 + n % 5))
    path = arms.save_arm(arm, tmp_path)
    loaded = arms.load_arm(arms.arm_path(tmp_path, "a"))
    assert path.name == "a.json" and loaded.records == arm.records and isinstance(next(iter(loaded.records)), int)
    with pytest.raises(BenchmarkError, match="already exists"):
        arms.save_arm(arm, tmp_path)
    arms.save_arm(arm, tmp_path, force=True)


@pytest.mark.parametrize("name", ["../evil", "a b", "", "x/y", "a\\b"])
def test_arm_names_cannot_escape_the_arms_folder(tmp_path, name):
    shot = make_snapshot(4)
    with pytest.raises(BenchmarkError, match="arm name"):
        arms.save_arm(make_arm(shot, lambda n, i: record(), name=name), tmp_path)


def test_loading_a_missing_arm_lists_what_exists(tmp_path):
    arms.save_arm(make_arm(make_snapshot(4), lambda n, i: record(), name="known"), tmp_path)
    with pytest.raises(BenchmarkError, match="known"):
        arms.load_arm(arms.arm_path(tmp_path, "unknown"))


# ---------- ordering: the PRODUCTION ranking ----------


def test_items_the_curator_never_shows_are_ranked_below_every_shown_candidate():
    shot = make_snapshot(8)
    per = {0: record("other", 5), 1: record("ai", 1), 2: record("ai", 2), 3: record("ai", 3)}
    arm = make_arm(shot, lambda n, item: per.get(n, record("ai", 2)))
    order = arms.production_order(shot, arm)
    eligible = [500 + n for n in range(8) if n not in (0, 1)]
    assert set(order.ranked[: len(eligible)]) == set(eligible)
    assert set(order.ranked[len(eligible) :]) == {500, 501}
    assert order.filtered == {500: "category is 'other'", 501: "importance is 1"}
    assert order.ranked.index(500) < order.ranked.index(501)  # among the filtered, the higher score first


def test_unscored_items_come_last_and_are_reported():
    shot = make_snapshot(6)
    arm = make_arm(shot, lambda n, item: record("ai", 3))
    del arm.records[502]
    order = arms.production_order(shot, arm)
    assert order.ranked[-1] == 502 and order.unscored == [502] and len(order.ranked) == 6


def test_popularity_is_part_of_the_production_order_but_not_of_the_model_only_order():
    signals = {0: {"hn": {"points": 1}}, 4: {"hn": {"points": 50}}, 8: {"hn": {"points": 500}}}
    shot = make_snapshot(12, signals)
    for item in shot.items:
        item["published_at"] = "2026-09-18T00:00:00+00:00"  # identical dates: nothing but popularity can separate them
    per = lambda n, item: record("ai", 3) if item["source"] == "hn" else record("ai", 2)  # noqa: E731
    order = arms.production_order(shot, make_arm(shot, per))
    assert order.ranked[:3] == [508, 504, 500]  # most popular Hacker News item first
    assert order.model_only[:3] == [500, 504, 508]  # the model alone cannot tell them apart; database order remains


def test_positions_are_zero_based_and_cover_every_item():
    shot = make_snapshot(6)
    order = arms.production_order(shot, make_arm(shot, lambda n, item: record("ai", 1 + n % 5)))
    assert sorted(order.positions().values()) == list(range(6)) and sorted(order.model_only_positions().values()) == list(range(6))


def test_continuous_relevance_breaks_ties_between_equal_integer_importance():
    shot = make_snapshot(6)
    for item in shot.items:
        item["signals"] = {"arxiv": {}}
        item["source"] = "arxiv"
    per = lambda n, item: record("ai", 3, relevance=0.3 + 0.1 * n)  # noqa: E731
    order = arms.production_order(shot, make_arm(shot, per, kind="jev"))
    assert order.ranked == [505, 504, 503, 502, 501, 500]


@pytest.mark.parametrize("kind", ["llm", "jev"])
def test_the_reconstructed_order_is_exactly_what_the_real_curator_would_show(kind):
    """CONTRACT TEST. The benchmark rebuilds "what production would surface" from stored arm output, including the
    one-line filter that lives inline in curate.prepare. This runs the REAL curator on the same data (its editor
    forced to fail so the deterministic top-N is what ships) and requires the same items in the same order.
    If production ranking ever changes and this file is not updated, this test fails instead of the benchmark
    quietly measuring something PIA no longer does."""
    n = 60
    signals = {}
    for i in range(n):
        source = ["hn", "arxiv", "hf_papers", "github"][i % 4]
        metric = {"hn": "points", "hf_papers": "upvotes", "github": "stars"}.get(source)
        signals[i] = {source: ({metric: (i * 37) % 200} if metric else {})}
        if i % 7 == 0:
            signals[i]["arxiv" if source != "arxiv" else "hn"] = {}  # reported by a second source: cross-source boost
    shot = make_snapshot(n, signals)
    categories = ["ai", "software", "research", "other"]
    per = lambda i, item: record(  # noqa: E731
        categories[(i * 5) % 4], 1 + (i * 3) % 5, relevance=(((i * 13) % 17) / 16 if kind == "jev" else None)
    )
    arm = make_arm(shot, per, kind=kind)
    order = arms.production_order(shot, arm)

    conn = arms.build_ranked_db(shot, arm)
    prepare = make_curator(SmartLLM(editor_error=LLMUnavailable("editor down")), triage=lambda conn, now: None)
    checkpoint = NOW - timedelta(days=5)
    content = prepare(conn, checkpoint, NOW, [])
    budget = headline_budget(5)
    shown = [row["id"] for row in content.shown]
    assert shown[:budget] == order.ranked[:budget]
    assert set(shown) == set(order.ranked[: budget + also_budget(budget)])


# ---------- re-scoring: a new formula, no new API calls ----------


def test_rescoring_with_the_unchanged_formula_reproduces_the_stored_scores(tmp_path, profile):
    shot = make_snapshot()
    arm = arms.run_jev_arm(shot, profile, FakeJev(lambda t, n: dict(match=n % 4, substance=(n * 3) % 4)), tmp_path, name="jev", now=NOW, workers=1, code=CODE)
    again = arms.rescore_jev_arm(arm, "jev-again", code=CODE)
    for item_id, original in arm.records.items():
        assert again.records[item_id]["relevance"] == pytest.approx(original["relevance"])
        assert again.records[item_id]["importance"] == original["importance"]
        assert again.records[item_id]["category"] == original["category"]
    assert again.meta["rescored_from"] == "jev" and again.meta["snapshot_id"] == shot.snapshot_id


def test_rescoring_applies_a_new_formula_to_the_same_raw_answers_without_calling_anyone(tmp_path, profile):
    shot = make_snapshot()
    client = FakeJev(lambda t, n: dict(match=n % 4))
    arm = arms.run_jev_arm(shot, profile, client, tmp_path, name="jev", now=NOW, workers=1, code=CODE)
    calls_before = len(client.calls)

    def inverted(answers):
        d = jt.derive(answers)
        return jt.Derived(1 - d.relevance, 1 + round(4 * (1 - d.relevance)), d.category, d.features, d.flags)

    flipped = arms.rescore_jev_arm(arm, "jev-flipped", derive_fn=inverted, code=CODE)
    assert len(client.calls) == calls_before
    for item_id, original in arm.records.items():
        assert flipped.records[item_id]["relevance"] == pytest.approx(1 - original["relevance"])
    assert flipped.records[500]["details"]["answers"] == arm.records[500]["details"]["answers"]  # raw data untouched


def test_only_a_jev_arm_can_be_rescored():
    shot = make_snapshot(4)
    with pytest.raises(BenchmarkError, match="raw Jev answers"):
        arms.rescore_jev_arm(make_arm(shot, lambda n, i: record(), kind="llm"), "x")


# ---------- provenance ----------


def test_code_version_never_fails_the_run(monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert arms.code_version() == {"commit": "unknown", "dirty": None}
