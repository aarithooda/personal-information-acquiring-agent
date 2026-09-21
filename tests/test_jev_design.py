"""Question set v2: what is asked, of whom, with what data, and how the answers are combined.

The design rules being tested come from TypeSafe's documentation (docs.typesafe.ai): one condition per Noul, Score
levels that are single-dimension situations, only the state a question needs (accuracy falls with unrelated context),
probabilities rather than interpolated Score decimals, confidence as metadata, and all arithmetic in code.
"""

import json
import re

import pytest

from jevfakes import SyntheticJev
from pia.jev import design as dz
from pia.profile import Profile

PROFILE = {
    "interest_areas": [{"name": "AI agents", "priority": "very_high", "specific_subtopics": ["tool use"]}],
    "technical_interests": {"very_high": ["agents"], "high": ["local models"]},
    "priority_hierarchy": {"tier_1": ["AI engineering"], "tier_2": ["Research"], "tier_3": ["Broader technology"], "priority_interpretation": "prose"},
    "building_interests": {"primary": ["A research scout", "An agent harness"], "secondary": ["A note-taking tool"], "project_relevance_rule": "prose"},
    "high_value_information": {"strongest_signals": ["New capability", "Deep result"], "additional_signals": ["Reproducible"], "low_noise_preference": "prose"},
    "low_value_information": {"usually_low_value": ["Marketing", "Generic news", "Hype"], "exceptions": ["A routine release that changes architecture"]},
    "discovery": {"strong_wildcards": ["Math meets computation", "New paradigms"], "wildcard_threshold": "prose"},
    "relevance_guidance": {"final_rule": "prose that should never be sent"},
}
ITEM = {"source": "hn", "title": "A title", "content_raw": "Some text"}
TITLE_ONLY = {"source": "hn", "title": "A title", "content_raw": None}


def plan(profile=PROFILE):
    return dz.DesignV2().plan(profile)


def by_name(p):
    return {r.name: r for r in p.requests}


def resolve(state, path):
    """Follow a documented backtick path such as `reader_profile.low_value_information.usually_low_value[1]`."""
    node = state
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        node = node[int(part[1:-1])] if part.startswith("[") else node[part]
    return node


# ---------- structure: requests and state slices ----------


def test_the_plan_has_one_request_per_concern_and_the_item_request_carries_no_profile():
    p = plan()
    assert [r.name for r in p.requests] == ["item", "interest", "value", "discovery"]
    assert by_name(p)["item"].profile is None
    state = p.state_for(by_name(p)["item"], dz.item_entry(ITEM))
    assert state == {"item": {"source": "Hacker News link", "title": "A title", "text": "Some text"}}  # nothing about the reader


def test_each_request_carries_only_the_profile_sections_its_questions_need():
    r = by_name(plan())
    assert set(r["interest"].profile) == {"interest_areas", "technical_interests", "priority_hierarchy", "building_interests"}
    assert set(r["value"].profile) == {"high_value_information", "low_value_information"}
    assert set(r["discovery"].profile) == {"discovery"}
    assert "relevance_guidance" not in json.dumps([x.profile for x in plan().requests])  # prose for a generalist model: never sent


def test_prose_fields_are_left_out_of_the_slices_because_only_lists_are_referenced():
    r = by_name(plan())
    assert "priority_interpretation" not in r["interest"].profile["priority_hierarchy"]
    assert "project_relevance_rule" not in r["interest"].profile["building_interests"]
    assert "low_noise_preference" not in r["value"].profile["high_value_information"]
    assert "wildcard_threshold" not in r["discovery"].profile["discovery"]


def test_every_backtick_path_in_every_question_resolves_inside_the_state_of_the_SAME_request():
    """The strongest structural guarantee: a question can never point at data that was not sent with it."""
    p = plan()
    for request in p.requests:
        state = p.state_for(request, dz.item_entry(ITEM))
        for qid, q in request.questions.items():
            texts = [q["instructions"]] + (list(q["criteria"].values()) if q["type"] == "choice" else [])
            for text in texts:
                for path in re.findall(r"`([^`]+)`", text):
                    assert resolve(state, path) is not None, f"{qid}: {path!r} does not resolve in the {request.name!r} request"


def test_the_list_entry_a_question_points_at_is_the_one_its_id_says():
    p = plan()
    q = by_name(p)["value"].questions
    state = p.state_for(by_name(p)["value"], dz.item_entry(ITEM))
    path = re.search(r"`([^`]+)`", q["low.pattern.1"]["instructions"]).group(1)
    assert resolve(state, path) == "Generic news"
    path = re.search(r"`([^`]+)`", q["signal.strongest_signals.0"]["instructions"]).group(1)
    assert resolve(state, path) == "New capability"


def test_question_ids_are_unique_across_requests_and_the_answers_can_be_merged():
    ids = [qid for r in plan().requests for qid in r.questions]
    assert len(ids) == len(set(ids))


# ---------- the questions follow the documented design rules ----------


def test_list_entries_become_one_atomic_noul_each_instead_of_one_disjunction_over_the_list():
    q = by_name(plan())["value"].questions
    assert sum(k.startswith("low.pattern.") for k in q) == 3 and sum(k.startswith("low.exception.") for k in q) == 1
    assert sum(k.startswith("signal.") for k in q) == 3
    assert all(v["type"] == "noul" for v in q.values())
    for v in q.values():
        assert " or " not in v["instructions"].lower().replace("`", ""), "a Noul must state ONE condition"


def test_priority_is_one_choice_over_the_profiles_own_tiers_plus_none_and_no_score_rubric_is_used():
    p = plan()
    choice = by_name(p)["interest"].questions["priority_tier"]
    assert choice["type"] == "choice" and list(choice["criteria"]) == ["tier_1", "tier_2", "tier_3", "none"]
    assert "priority_hierarchy.tier_1" in choice["criteria"]["tier_1"]
    assert all(q["type"] != "score" for r in p.requests for q in r.questions.values())  # no interpolated decimals feed the formula


def test_the_number_of_tiers_and_lists_follows_the_profile_not_a_hard_coded_shape():
    small = {"priority_hierarchy": {"tier_1": ["a"], "tier_2": ["b"]}, "low_value_information": {"usually_low_value": ["x"]}}
    p = plan(small)
    assert list(by_name(p)["interest"].questions["priority_tier"]["criteria"]) == ["tier_1", "tier_2", "none"]
    assert [r.name for r in p.requests] == ["item", "interest", "value"]  # no discovery section, so no discovery request
    assert set(by_name(p)["value"].questions) == {"low.pattern.0"}  # no signals or exceptions asked about


def test_a_profile_with_no_way_to_reach_relevance_is_refused_with_a_clear_message():
    with pytest.raises(dz.DesignError, match="priority tiers, building interests or wildcards"):
        plan({"low_value_information": {"usually_low_value": ["x"]}})


def test_the_item_request_asks_category_injection_and_whether_the_subject_is_unclear():
    q = by_name(plan())["item"].questions
    assert set(q) == {"category", "injection", "subject_unclear"}
    assert q["category"]["type"] == "choice" and "other" in q["category"]["criteria"]  # the docs: always offer an 'other'


def test_no_question_asks_about_title_only_ness_because_the_code_already_knows_that():
    ids = " ".join(qid for r in plan().requests for qid in r.questions)
    assert "title" not in ids and "too_little" not in ids


# ---------- combining the answers ----------


def answers_for(**overrides):
    """Run the plan against a SyntheticJev and return (answers, structure): the exact stored form."""
    p = plan()
    jev = SyntheticJev(**overrides)
    merged = {}
    for request in p.requests:
        response = jev.system_one(p.state_for(request, dz.item_entry(ITEM)), request.questions)
        merged.update({k: v.model_dump() for k, v in response.answers.items()})
    return merged, p.structure


def derive(has_text=True, **overrides):
    answers, structure = answers_for(**overrides)
    return dz.DesignV2().derive(answers, structure, {"title_only": not has_text})


TIER1 = {"priority_tier": {"tier_1": 1.0}}
NONE = {"priority_tier": {"none": 1.0}}


def test_relevance_is_a_probability_like_number_between_zero_and_one():
    for kw in (dict(choice=TIER1, noul={"signal.": 1.0}), dict(choice=NONE, base=0.0), dict(choice=TIER1, noul={"low.pattern.": 1.0})):
        assert 0.0 <= derive(**kw).relevance <= 1.0


def test_priority_is_the_expected_weight_of_the_tier_distribution_using_neutral_rank_weights():
    top, second, third, none = (derive(choice={"priority_tier": {t: 1.0}}, base=0.0).features["interest"] for t in ("tier_1", "tier_2", "tier_3", "none"))
    assert (top, second, third, none) == pytest.approx((1.0, 2 / 3, 1 / 3, 0.0))  # (n - rank) / n for three tiers
    mixed = derive(choice={"priority_tier": {"tier_1": 0.5, "none": 0.5}}, base=0.0).features["interest"]
    assert mixed == pytest.approx(0.5)  # an expectation over the distribution, not just the winner


def test_any_one_route_is_enough_to_reach_relevance():
    interest = derive(choice=TIER1, base=0.0)
    build = derive(choice=NONE, noul={"build.": 0.0, "build.primary.0": 1.0}, base=0.0)
    wildcard = derive(choice=NONE, noul={"wildcard.1": 1.0}, base=0.0)
    for d in (interest, build, wildcard):
        assert d.features["reach"] == pytest.approx(1.0)
    assert {interest.extra["evidence"]["reach_by"], build.extra["evidence"]["reach_by"], wildcard.extra["evidence"]["reach_by"]} == {"priority_tier", "build.primary.0", "wildcard.1"}


def test_secondary_lists_count_for_less_than_primary_ones_by_the_same_rank_rule():
    primary = derive(choice=NONE, noul={"build.primary.0": 1.0}, base=0.0).features["build"]
    secondary = derive(choice=NONE, noul={"build.secondary.0": 1.0}, base=0.0).features["build"]
    assert primary == pytest.approx(1.0) and secondary == pytest.approx(0.5)


def test_a_reader_signal_scales_relevance_but_can_at_most_halve_it():
    none = derive(choice=TIER1, noul={"signal.": 0.0}, base=0.0).relevance
    full = derive(choice=TIER1, noul={"signal.": 1.0}, base=0.0).relevance
    assert full == pytest.approx(1.0) and none == pytest.approx(dz.VALUE_FLOOR)
    assert none >= 0.5 * full  # evidence modulates; it never gates, so unobservable evidence cannot erase a strong fit


def test_an_off_topic_item_stays_off_topic_however_substantive():
    """v1's additive blend paid 35% credit for substance alone; here substance without any route to relevance is worth nothing."""
    assert derive(choice=NONE, noul={"signal.": 1.0}, base=0.0).relevance == 0.0


def test_a_low_value_pattern_removes_relevance_unless_an_exception_excuses_it():
    clean = derive(choice=TIER1, noul={"low.": 0.0}, base=0.0).relevance
    hit = derive(choice=TIER1, noul={"low.pattern.1": 0.9, "low.exception.": 0.0}, base=0.0).relevance
    excused = derive(choice=TIER1, noul={"low.pattern.1": 0.9, "low.exception.": 0.9}, base=0.0).relevance
    assert hit == pytest.approx(clean * (1 - 0.9))  # the penalty is the probability that the pattern applies
    assert hit < excused <= clean
    assert derive(choice=TIER1, noul={"low.pattern.1": 0.9}, base=0.0).extra["evidence"]["penalty_by"] == "low.pattern.1"


def test_the_strongest_pattern_decides_not_the_number_of_weak_ones():
    """Correlated, weakly-calibrated probabilities are combined with max, so thirteen 0.2s do not add up to a penalty."""
    many_weak = derive(choice=TIER1, noul={"low.pattern.": 0.2}, base=0.0).features["low_value"]
    one_strong = derive(choice=TIER1, noul={"low.pattern.0": 0.6, "low.pattern.": 0.0}, base=0.0).features["low_value"]
    assert many_weak == pytest.approx(0.2) and one_strong == pytest.approx(0.6)


def test_a_near_certain_injection_zeroes_relevance_and_is_flagged():
    innocent = derive(choice=TIER1, noul={"injection": 0.74, "signal.": 1.0}, base=0.0)
    hostile = derive(choice=TIER1, noul={"injection": 0.97, "signal.": 1.0}, base=0.0)
    assert innocent.relevance > 0 and hostile.relevance == 0.0 and "possible_injection" in hostile.flags


def test_ranking_is_monotone_in_every_input():
    kw = dict(choice={"priority_tier": {"tier_2": 1.0}}, noul={"signal.": 0.4, "low.pattern.": 0.2, "build.": 0.1, "wildcard.": 0.1}, base=0.0)
    r0 = derive(**kw).relevance
    assert derive(**{**kw, "noul": {**kw["noul"], "signal.": 0.8}}).relevance > r0  # stronger signal
    assert derive(**{**kw, "noul": {**kw["noul"], "low.pattern.": 0.05}}).relevance > r0  # weaker low-value pattern
    assert derive(**{**kw, "choice": {"priority_tier": {"tier_1": 1.0}}}).relevance > r0  # higher priority tier
    assert derive(**{**kw, "noul": {**kw["noul"], "build.": 0.5}}).relevance == r0  # a route below the best one (interest is 2/3 here) changes nothing: max, not a sum
    assert derive(**{**kw, "noul": {**kw["noul"], "build.": 0.9}}).relevance > r0  # ...but a route ABOVE it takes over


# ---------- title-only items: no hidden penalty and no hidden bonus ----------


def test_relevance_does_not_depend_on_whether_the_item_had_text_given_identical_answers():
    for kw in (dict(choice=TIER1, noul={"signal.": 0.3}), dict(choice={"priority_tier": {"tier_2": 0.6, "none": 0.4}}, base=0.2)):
        assert derive(has_text=True, **kw).relevance == derive(has_text=False, **kw).relevance


def test_the_title_only_fact_and_the_models_own_uncertainty_are_recorded_beside_the_score_not_inside_it():
    d = derive(has_text=False, choice=TIER1, noul={"subject_unclear": 0.7})
    assert d.extra["facts"] == {"title_only": True}
    assert d.features["subject_unclear"] == pytest.approx(0.7) and d.features["tier_confidence"] == pytest.approx(0.8)
    assert derive(has_text=True, choice=TIER1).extra["facts"] == {"title_only": False}
    assert derive(has_text=True, choice=TIER1, noul={"subject_unclear": 0.7}).relevance == derive(has_text=True, choice=TIER1, noul={"subject_unclear": 0.0}).relevance


# ---------- category and importance ----------


def test_category_is_the_most_likely_option_and_a_relevant_other_is_filed_under_its_closest_real_area():
    cat = {"category": {"ai": 0.1, "software": 0.3, "research": 0.1, "other": 0.5}}
    boring = derive(choice={**NONE, **cat}, base=0.0)
    relevant = derive(choice={**TIER1, **cat}, noul={"signal.": 1.0}, base=0.0)
    assert boring.category == "other" and relevant.category == "software"


def test_importance_uses_the_same_one_to_five_mapping_as_v1():
    assert derive(choice=NONE, base=0.0).importance == 1
    assert derive(choice=TIER1, noul={"signal.": 1.0}, base=0.0).importance == 5


# ---------- inspectability and re-derivation ----------


def test_the_decision_can_be_traced_to_the_questions_that_drove_it():
    d = derive(choice=NONE, noul={"build.primary.1": 0.8, "signal.strongest_signals.1": 0.6, "low.pattern.2": 0.4, "build.": 0.0, "signal.": 0.0, "low.": 0.0}, base=0.0)
    ev = d.extra["evidence"]
    assert (ev["reach_by"], ev["value_by"], ev["penalty_by"]) == ("build.primary.1", "signal.strongest_signals.1", "low.pattern.2")


def test_derive_needs_only_the_stored_answers_the_stored_structure_and_the_facts():
    answers, structure = answers_for(choice=TIER1, noul={"signal.": 0.7})
    stored = json.loads(json.dumps({"answers": answers, "plan": structure}))  # what the database holds
    again = dz.DesignV2().derive(stored["answers"], stored["plan"], {"title_only": False})
    assert again.relevance == pytest.approx(derive(choice=TIER1, noul={"signal.": 0.7}).relevance)


def test_a_profile_object_compiles_through_the_same_path_as_the_dict():
    profile = Profile(data={"priority_hierarchy": {"tier_1": ["a"]}, "core_interest_areas": {"area": [{"name": "X", "interest_character": "personal"}]}}, hash="h")
    p = dz.DesignV2().plan(profile.jev_state())
    assert "interest_character" not in json.dumps([r.profile for r in p.requests])


def test_the_committed_example_profile_works_with_the_current_design():
    """config/interests.example.toml is what a new user starts from: it must compile, and every question must point at real data."""
    from pathlib import Path

    from pia.profile import load_profile

    example = Path(__file__).parent.parent / "config" / "interests.example.toml"
    p = dz.DesignV2().plan(load_profile(example).jev_state())
    assert {r.name for r in p.requests} >= {"item", "interest", "value", "discovery"}
    for request in p.requests:
        state = p.state_for(request, dz.item_entry(ITEM))
        for q in request.questions.values():
            for text in [q["instructions"], *(q["criteria"].values() if q["type"] == "choice" else [])]:
                for path in re.findall(r"`([^`]+)`", text):
                    assert resolve(state, path) is not None
