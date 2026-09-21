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
