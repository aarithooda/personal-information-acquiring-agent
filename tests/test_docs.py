"""Keep the README honest: it must cover every command, and its examples must actually work."""

import re
from pathlib import Path

from typer.main import get_command

from pia.cli import app
from pia.sources.registry import load_sources

ROOT = Path(__file__).parent.parent
README = ROOT / "README.md"


def readme_text() -> str:
    assert README.exists(), "README.md is missing"
    return README.read_text(encoding="utf-8")


def test_the_readme_documents_every_cli_command():
    commands = set(get_command(app).commands)
    assert {"collect", "enrich", "status", "history", "show", "doctor"} <= commands
    text = readme_text()
    missing = [name for name in commands if f"pia {name}" not in text]
    assert not missing, f"README does not mention: {missing}"


def test_the_readme_example_for_adding_a_source_is_valid_config(tmp_path):
    blocks = re.findall(r"```toml\n(.*?)```", readme_text(), re.S)
    assert blocks, "README has no ```toml example"
    config = tmp_path / "sources.toml"
    config.write_text(blocks[0], encoding="utf-8")
    assert load_sources(config), "the README example defines no sources"


def test_the_readme_points_at_the_launcher_and_the_design_notes():
    text = readme_text()
    assert "run-pia.bat" in text and "docs/design.md" in text


def test_the_readme_is_honest_that_article_fetching_is_not_built():
    text = readme_text().lower()
    assert "article" in text and "not implemented" in text


def test_the_readme_documents_the_jev_setup_and_privacy_of_the_profile():
    text = readme_text()
    for needed in ("TYPESAFE_API_KEY", "config/interests.toml", "--triage", "interests.example.toml", "Sent to Jev"):
        assert needed in text, needed


def test_the_project_is_named_consistently():
    """The public name is "Personal Information Acquiring Agent" (short form PIA); `pia` stays the technical identifier.

    Only public documentation and packaging metadata are checked. Nothing under src/ is: the evaluated source tree is frozen
    (benchmark_v2/FREEZE.json), so user-facing strings inside it are deliberately left as they were when it was evaluated."""
    import tomllib

    name = "Personal Information Acquiring Agent"
    assert readme_text().splitlines()[0].startswith(f"# {name}")
    assert tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["description"].startswith(name)
    stale = [
        str(path.relative_to(ROOT))
        for path in [README, ROOT / "pyproject.toml", *(ROOT / "docs").glob("*.md")]
        if "Personal Intelligence Agent" in path.read_text(encoding="utf-8")
    ]
    assert not stale, f"the old project name is still used in: {stale}"
