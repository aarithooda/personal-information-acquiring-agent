"""Keep the documentation honest: it must cover every command, its examples must work, it must say what is not
done, and it must not contain personal details."""

import re
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from typer.main import get_command

from pia.acquire.adapters import build_adapter
from pia.cli import app
from pia.store import db, seed

ROOT = Path(__file__).parent.parent
README = ROOT / "README.md"
PLATFORM_DOCS = ROOT / "docs" / "platform"


def readme_text() -> str:
    assert README.exists(), "README.md is missing"
    return README.read_text(encoding="utf-8")


def test_the_readme_documents_every_cli_command():
    commands = set(get_command(app).commands)
    assert {"collect", "enrich", "status", "history", "show", "doctor", "serve", "run-once", "import-legacy", "set-password"} <= commands
    text = readme_text()
    missing = [name for name in commands if f"pia {name}" not in text]
    assert not missing, f"README does not mention: {missing}"


def test_the_readme_example_of_a_catalogue_source_is_valid_and_fetchable_config():
    """The README shows one catalogue entry; it must seed, and the adapter it describes must build."""
    blocks = re.findall(r"```toml\n(.*?)```", readme_text(), re.S)
    assert blocks, "README has no ```toml example"
    conn = db.connect(":memory:")
    assert seed.seed_sources(conn, tomllib.loads(blocks[0]), datetime(2026, 11, 15, tzinfo=timezone.utc)) == 1
    row = conn.execute("SELECT * FROM sources").fetchone()
    assert row["status"] == "active" and build_adapter(row).url == row["url"]


def test_the_readme_points_at_the_launcher_and_the_design_notes():
    text = readme_text()
    assert "run-pia.bat" in text and "docs/design.md" in text and "docs/platform/ARCHITECTURE.md" in text


def test_the_readme_is_honest_about_what_is_not_done():
    """The README must not read as production-ready while deployment and account setup remain."""
    text = readme_text()
    for needed in ("not deployed", "need a one-time setup by you", "docs/platform/ROADMAP.md", "## Limitations", "No labelled evaluation"):
        assert needed in text, needed


def test_the_readme_makes_no_claim_that_jev_is_superior():
    text = readme_text()
    assert "could\n**not** show it is better" in text or "could **not** show it is better" in text
    assert "docs/evaluation.md" in text


def test_the_readme_documents_the_jev_setup_and_privacy_of_the_profile():
    text = readme_text()
    for needed in ("TYPESAFE_API_KEY", "config/interests.toml", "--triage", "interests.example.toml", "Sent to Jev"):
        assert needed in text, needed


def test_the_platform_documents_exist_and_the_roadmap_tracks_status():
    for name in ("PRD.md", "AUDIT.md", "ARCHITECTURE.md", "ROADMAP.md", "SOURCES.md", "OPERATIONS.md", "GMAIL.md"):
        assert (PLATFORM_DOCS / name).is_file(), name
    roadmap = (PLATFORM_DOCS / "ROADMAP.md").read_text(encoding="utf-8")
    for needed in ("| Tested |", "| Implemented |", "| Blocked |", "Open questions for the owner", "Handoff notes"):
        assert needed in roadmap, needed
    prd = (PLATFORM_DOCS / "PRD.md").read_text(encoding="utf-8")
    for marker in ("**[A]**", "**[P]**", "**[Q]**", "**[D]**"):  # agreed, proposed, open question, deferred
        assert marker in prd, marker


def test_no_personal_details_are_in_tracked_files():
    """The repository is public: the owner's institution and private files belong only in git-ignored paths."""
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    assert not {"config/personal.toml", "config/interests.toml", ".env", ".env.txt"} & set(tracked)
    assert not [name for name in tracked if name.startswith("data/")]
    # The words to look for come from the owner's own git-ignored file, so this test never names them itself.
    personal = seed.load_toml(seed.PERSONAL_FILE).get("university", {})
    private_terms = [term for term in (personal.get("name"), personal.get("short")) if term and len(term) > 3]
    offenders = []
    for name in tracked:
        path = ROOT / name
        if path.suffix in (".md", ".toml", ".py", ".js", ".html", ".css", ".ps1", ".bat") and path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(term in text for term in private_terms):
                offenders.append(name)
    assert not offenders, offenders


def test_secret_files_are_git_ignored_and_the_example_holds_no_key():
    ignored = subprocess.run(["git", "check-ignore", ".env", ".env.txt", ".env.local", "data/platform.db", "data/secrets/gmail.json", "config/personal.toml"],
                             cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert ignored == [".env", ".env.txt", ".env.local", "data/platform.db", "data/secrets/gmail.json", "config/personal.toml"]
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            assert line.split("=", 1)[1].strip() == "", f"the example env file must not contain a value: {line.split('=')[0]}"


def test_the_documents_describe_groq_only_operation():
    text = readme_text()
    assert "Groq is the only external model provider" in text and "dormant" in text and "[providers]" in text
    architecture = (PLATFORM_DOCS / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "Groq only" in architecture and "dormant" in architecture


def test_the_project_is_named_consistently():
    """The public name is "Personal Information Acquiring Agent" (short form PIA); `pia` stays the technical identifier."""
    name = "Personal Information Acquiring Agent"
    assert readme_text().splitlines()[0].startswith(f"# {name}")
    assert tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["description"].startswith(name)
    stale = [
        str(path.relative_to(ROOT))
        for path in [README, ROOT / "pyproject.toml", *(ROOT / "docs").glob("*.md"), *PLATFORM_DOCS.glob("*.md")]
        if "Personal Intelligence Agent" in path.read_text(encoding="utf-8")
    ]
    assert not stale, f"the old project name is still used in: {stale}"
