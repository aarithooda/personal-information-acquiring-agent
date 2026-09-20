import json

import pytest
from fakes import FakeLLM, triaged_row

from pia.llm.client import LLMError
from pia.llm.headlines import select_headlines


def pick(index, explanation="Explained.", why="It matters."):
    return {"index": index, "explanation": explanation, "why_it_matters": why}


def answer(*picks):
    return {"headlines": list(picks)}


CANDIDATES = [triaged_row(i) for i in range(1, 6)]


def test_the_editor_sees_every_candidate_and_the_slot_limit():
    llm = FakeLLM(answer(pick(0)))
    select_headlines(llm, CANDIDATES, max_count=3)
    call = llm.calls[0]
    assert call["model"] == "openai/gpt-oss-120b"
    for i in range(1, 6):
        assert f"Title {i}" in call["user"] and f"Summary {i}." in call["user"]
    assert "at most 3" in call["user"]
    assert "untrusted" in call["system"].lower()
    assert call["schema"]["required"] == ["headlines"]


def test_results_map_back_to_candidates_in_the_editors_order():
    llm = FakeLLM(answer(pick(2, "Third first."), pick(0, "Then the first.")))
    headlines = select_headlines(llm, CANDIDATES, max_count=5)
    assert [h.row["id"] for h in headlines] == [3, 1]
    assert headlines[0].explanation == "Third first."
    assert headlines[0].why_it_matters == "It matters."


def test_the_editor_may_choose_fewer_than_the_limit_when_little_happened():
    headlines = select_headlines(FakeLLM(answer(pick(1))), CANDIDATES, max_count=5)
    assert len(headlines) == 1


def test_extra_picks_beyond_the_limit_are_dropped():
    llm = FakeLLM(answer(pick(0), pick(1), pick(2), pick(3)))
    assert len(select_headlines(llm, CANDIDATES, max_count=2)) == 2


def test_invalid_picks_are_dropped_but_valid_ones_survive():
    llm = FakeLLM(
        answer(
            pick(0),
            pick(0, "Duplicate index."),
            pick(99, "No such candidate."),
            pick(-1, "Negative."),
            pick(1, "   ", "Blank explanation."),
            pick(2, "Good.", "  "),  # blank 'why' is tolerated
        )
    )
    headlines = select_headlines(llm, CANDIDATES, max_count=5)
    assert [h.row["id"] for h in headlines] == [1, 3]
    assert headlines[1].why_it_matters is None


def test_a_response_with_no_usable_picks_is_an_error_so_the_caller_can_fall_back():
    with pytest.raises(LLMError):
        select_headlines(FakeLLM(answer(pick(99))), CANDIDATES, max_count=3)


def test_no_candidates_means_no_llm_call():
    llm = FakeLLM(answer(pick(0)))
    assert select_headlines(llm, [], max_count=3) == []
    assert llm.calls == []


def test_candidate_text_is_sent_as_json_data():
    llm = FakeLLM(answer(pick(0)))
    select_headlines(llm, CANDIDATES[:1], max_count=1)
    payload = llm.calls[0]["user"].split("<candidates>")[1].split("</candidates>")[0]
    assert json.loads(payload)[0]["title"] == "Title 1"
