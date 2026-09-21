import json
from pathlib import Path

import pytest

from pia.profile import Profile, ProfileError, approx_tokens, load_profile

ROOT = Path(__file__).parent.parent
EXAMPLE = ROOT / "config" / "interests.example.toml"

TOML = """
# a comment the loader must ignore
[profile]
name = "SECRET_PERSON_NAME"
purpose = "meta"
version = "1.0"
user_stage = "Second-year student."
core_orientation = "Build things."

[core_interest_areas]
[[core_interest_areas.area]]
name = "AI agents"
priority = "very_high"
specific_subtopics = ["tool use", "evaluation"]
especially_interesting = ["New agent architectures"]
interest_character = "Practical and intellectual."

[building_interests]
primary = ["developer tools"]
project_relevance_rule = "Extra relevance if it could become a component."
current_building_evidence = ["A private project name"]

[low_value_information]
usually_low_value = ["Routine announcements"]
exceptions = ["Unless it changes architecture."]

[learning_orientation]
learning_preference = ["derivation"]
"""


def write(tmp_path, text=TOML) -> Path:
    path = tmp_path / "interests.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_and_exposes_the_parsed_data(tmp_path):
    profile = load_profile(write(tmp_path))
    assert isinstance(profile, Profile)
    assert profile.data["core_interest_areas"]["area"][0]["name"] == "AI agents"


def test_the_hash_identifies_the_content_not_the_formatting(tmp_path):
    base = load_profile(write(tmp_path)).hash
    reformatted = write(tmp_path, TOML.replace("# a comment the loader must ignore", "# different comment\n\n\n"))
    assert load_profile(reformatted).hash == base  # comments and blank lines do not change it
    edited = write(tmp_path, TOML.replace("Routine announcements", "Routine product announcements"))
    assert load_profile(edited).hash != base
    assert len(base) == 12 and all(c in "0123456789abcdef" for c in base)


def test_jev_state_keeps_the_decision_relevant_parts_and_renames_the_areas(tmp_path):
    state = load_profile(write(tmp_path)).jev_state()
    assert state["interest_areas"] == [
        {"name": "AI agents", "priority": "very_high", "specific_subtopics": ["tool use", "evaluation"], "especially_interesting": ["New agent architectures"]}
    ]
    assert state["low_value_information"]["exceptions"] == ["Unless it changes architecture."]
    assert state["building_interests"]["project_relevance_rule"].startswith("Extra relevance")


def test_jev_state_leaves_out_personal_and_non_decision_fields(tmp_path):
    state = load_profile(write(tmp_path)).jev_state()
    blob = json.dumps(state)
    for absent in ("SECRET_PERSON_NAME", "Second-year student", "Build things.", "Practical and intellectual", "A private project name", "derivation"):
        assert absent not in blob, absent
    assert "profile" not in state and "learning_orientation" not in state


def test_compiling_does_not_mutate_the_loaded_data(tmp_path):
    profile = load_profile(write(tmp_path))
    before = json.dumps(profile.data, sort_keys=True)
    profile.jev_state()
    profile.llm_text()
    assert json.dumps(profile.data, sort_keys=True) == before


def test_llm_text_is_readable_deterministic_and_includes_the_editors_context_but_not_the_name(tmp_path):
    profile = load_profile(write(tmp_path))
    text = profile.llm_text()
    assert text == profile.llm_text()
    assert "SECRET_PERSON_NAME" not in text
    assert "Second-year student." in text and "Build things." in text  # useful for 'why it matters to you'
    assert "AI agents" in text and "very_high" in text.replace(" ", "_") or "very high" in text
    assert "Unless it changes architecture." in text and "derivation" in text
    assert "comment" not in text.lower()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "none of the expected sections"),
        ("[profile]\nname = 'x'\n", "none of the expected sections"),
        ("[unrelated]\nkey = 1\n", "none of the expected sections"),
        ("this is = = not toml", "not valid TOML"),
    ],
)
def test_unusable_profiles_are_rejected_with_a_clear_message(tmp_path, content, message):
    with pytest.raises(ProfileError, match=message):
        load_profile(write(tmp_path, content))


def test_a_missing_file_says_where_to_put_it(tmp_path):
    with pytest.raises(ProfileError, match=r"interests\.example\.toml"):
        load_profile(tmp_path / "nope.toml")


def test_the_committed_example_is_a_valid_profile_and_small():
    profile = load_profile(EXAMPLE)
    assert approx_tokens(json.dumps(profile.jev_state())) < 1500  # a good profile is short


def test_the_users_real_profile_still_loads_if_present():
    real = ROOT / "config" / "interests.toml"
    if not real.exists():
        pytest.skip("no personal profile on this machine")
    profile = load_profile(real)
    assert profile.jev_state()["interest_areas"]


def test_token_estimate_is_a_rough_quarter_of_the_characters():
    assert approx_tokens("a" * 400) == 100
