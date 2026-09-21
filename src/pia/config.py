"""Where things live, and how secrets are loaded.

Everything is relative to the project root, not the current directory.
"""

import codecs
import os
from collections.abc import Mapping
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "pia.db"
DEFAULT_SOURCES = PROJECT_ROOT / "config" / "sources.toml"

# Files searched for secrets, in order. ".env.txt" is there because Windows Notepad
# quietly appends ".txt" when you save a file named ".env".
ENV_FILES = (".env", ".env.txt")


class ConfigError(Exception):
    """Configuration is missing or unusable. Messages never contain secret values."""


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def _parse_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ").strip()
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip("\"'")
    return values


def find_groq_api_key(root: Path = PROJECT_ROOT, environ: Mapping[str, str] = os.environ) -> tuple[str, str]:
    """Returns (key, where it came from). The real environment wins; otherwise the first
    .env file that has a non-empty value."""
    if environ.get("GROQ_API_KEY"):
        return environ["GROQ_API_KEY"], "environment variable"
    for name in ENV_FILES:
        path = root / name
        if path.is_file():
            value = _parse_env_file(path).get("GROQ_API_KEY")
            if value:
                return value, name
    raise ConfigError(
        f"GROQ_API_KEY not found. Put a line 'GROQ_API_KEY=your_key' in {root / '.env'} "
        "(or set the environment variable)."
    )


def get_groq_api_key(root: Path = PROJECT_ROOT, environ: Mapping[str, str] = os.environ) -> str:
    return find_groq_api_key(root, environ)[0]
