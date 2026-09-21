import pytest

from pia.config import ConfigError, get_groq_api_key

KEY = "gsk_test_1234567890"


def test_environment_variable_wins(tmp_path):
    (tmp_path / ".env").write_text("GROQ_API_KEY=from_file\n")
    assert get_groq_api_key(root=tmp_path, environ={"GROQ_API_KEY": KEY}) == KEY


def test_reads_dot_env(tmp_path):
    (tmp_path / ".env").write_text(f"GROQ_API_KEY={KEY}\n")
    assert get_groq_api_key(root=tmp_path, environ={}) == KEY


def test_falls_back_to_dot_env_txt_because_notepad_adds_the_extension(tmp_path):
    (tmp_path / ".env.txt").write_text(f"GROQ_API_KEY={KEY}\n")
    assert get_groq_api_key(root=tmp_path, environ={}) == KEY


@pytest.mark.parametrize(
    "line",
    [f'GROQ_API_KEY="{KEY}"', f"GROQ_API_KEY='{KEY}'", f"  GROQ_API_KEY = {KEY}  ", f"export GROQ_API_KEY={KEY}"],
)
def test_tolerates_quotes_spaces_and_export(tmp_path, line):
    (tmp_path / ".env").write_text(f"# my keys\nOTHER=1\n{line}\n")
    assert get_groq_api_key(root=tmp_path, environ={}) == KEY


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_handles_the_encodings_windows_editors_produce(tmp_path, encoding):
    (tmp_path / ".env.txt").write_text(f"GROQ_API_KEY={KEY}\r\n", encoding=encoding)
    assert get_groq_api_key(root=tmp_path, environ={}) == KEY


def test_missing_key_is_a_clear_error_that_says_where_to_put_it(tmp_path):
    with pytest.raises(ConfigError, match=r"\.env"):
        get_groq_api_key(root=tmp_path, environ={})


def test_an_empty_value_counts_as_missing_and_errors_do_not_echo_file_contents(tmp_path):
    (tmp_path / ".env").write_text("GROQ_API_KEY=\nSECRET_OTHER=hunter2\n")
    with pytest.raises(ConfigError) as excinfo:
        get_groq_api_key(root=tmp_path, environ={})
    assert "hunter2" not in str(excinfo.value)


def test_find_reports_where_the_key_came_from_without_exposing_it(tmp_path):
    from pia.config import find_groq_api_key

    assert find_groq_api_key(root=tmp_path, environ={"GROQ_API_KEY": KEY}) == (KEY, "environment variable")
    (tmp_path / ".env.txt").write_text(f"GROQ_API_KEY={KEY}\n")
    assert find_groq_api_key(root=tmp_path, environ={}) == (KEY, ".env.txt")
    (tmp_path / ".env").write_text(f"GROQ_API_KEY={KEY}\n")
    assert find_groq_api_key(root=tmp_path, environ={})[1] == ".env"  # .env takes priority


# ---------- Jev key: the docs call the variable TYPESAFE_API_KEY, users may have written JEV_API_KEY ----------

JEV = "jev_test_key_123456"


def test_jev_key_is_read_from_either_variable_name_in_a_file(tmp_path):
    from pia.config import find_jev_api_key

    (tmp_path / ".env.txt").write_text(f"GROQ_API_KEY={KEY}\nJEV_API_KEY={JEV}\n")
    assert find_jev_api_key(root=tmp_path, environ={}) == (JEV, ".env.txt")
    (tmp_path / ".env.txt").write_text(f"TYPESAFE_API_KEY={JEV}\n")
    assert find_jev_api_key(root=tmp_path, environ={}) == (JEV, ".env.txt")


def test_the_documented_name_wins_and_the_real_environment_wins_over_files(tmp_path):
    from pia.config import find_jev_api_key

    (tmp_path / ".env").write_text("JEV_API_KEY=alias_key\nTYPESAFE_API_KEY=official_key\n")
    assert find_jev_api_key(root=tmp_path, environ={})[0] == "official_key"
    assert find_jev_api_key(root=tmp_path, environ={"JEV_API_KEY": "from_env"}) == ("from_env", "environment variable")


def test_a_missing_jev_key_says_which_names_are_accepted_and_never_echoes_other_secrets(tmp_path):
    from pia.config import find_jev_api_key

    (tmp_path / ".env").write_text(f"GROQ_API_KEY={KEY}\n")
    with pytest.raises(ConfigError) as excinfo:
        find_jev_api_key(root=tmp_path, environ={})
    assert "TYPESAFE_API_KEY" in str(excinfo.value) and KEY not in str(excinfo.value)


def test_groq_lookup_is_unchanged_by_the_refactor(tmp_path):
    (tmp_path / ".env").write_text(f"JEV_API_KEY={JEV}\nGROQ_API_KEY={KEY}\n")
    assert get_groq_api_key(root=tmp_path, environ={}) == KEY


# ---------- Jev model pinning: aliases such as jev-latest move with releases ----------


def test_the_jev_model_can_be_pinned_from_a_file_or_the_environment_and_defaults_to_none(tmp_path):
    from pia.config import get_jev_model

    assert get_jev_model(root=tmp_path, environ={}) is None  # nothing configured: the client's default alias applies
    (tmp_path / ".env").write_text("JEV_MODEL=jev-1.13.0\n")
    assert get_jev_model(root=tmp_path, environ={}) == "jev-1.13.0"
    assert get_jev_model(root=tmp_path, environ={"JEV_MODEL": "jev-1.14.0"}) == "jev-1.14.0"  # the real environment wins
