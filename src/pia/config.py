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
DEFAULT_PROFILE = PROJECT_ROOT / "config" / "interests.toml"  # the reader's interests (git-ignored, personal)

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


def _find_key(names: tuple[str, ...], root: Path, environ: Mapping[str, str], missing_hint: str) -> tuple[str, str]:
    """Returns (key, where it came from). The real environment wins; otherwise the first .env file that
    has a non-empty value. `names` are tried in order, so the first one is the preferred spelling."""
    for name in names:
        if environ.get(name):
            return environ[name], "environment variable"
    for file_name in ENV_FILES:
        path = root / file_name
        if path.is_file():
            values = _parse_env_file(path)
            for name in names:
                if values.get(name):
                    return values[name], file_name
    raise ConfigError(missing_hint)


def find_groq_api_key(root: Path = PROJECT_ROOT, environ: Mapping[str, str] = os.environ) -> tuple[str, str]:
    return _find_key(
        ("GROQ_API_KEY",),
        root,
        environ,
        f"GROQ_API_KEY not found. Put a line 'GROQ_API_KEY=your_key' in {root / '.env'} (or set the environment variable).",
    )


def get_groq_api_key(root: Path = PROJECT_ROOT, environ: Mapping[str, str] = os.environ) -> str:
    return find_groq_api_key(root, environ)[0]


# TypeSafe's documentation calls the variable TYPESAFE_API_KEY; JEV_API_KEY is accepted as an alias.
JEV_KEY_NAMES = ("TYPESAFE_API_KEY", "JEV_API_KEY")


def find_jev_api_key(root: Path = PROJECT_ROOT, environ: Mapping[str, str] = os.environ) -> tuple[str, str]:
    return _find_key(
        JEV_KEY_NAMES,
        root,
        environ,
        f"TYPESAFE_API_KEY (or JEV_API_KEY) not found. Put a line 'TYPESAFE_API_KEY=your_key' in {root / '.env'}.",
    )


def get_jev_api_key(root: Path = PROJECT_ROOT, environ: Mapping[str, str] = os.environ) -> str:
    return find_jev_api_key(root, environ)[0]


def get_jev_model(root: Path = PROJECT_ROOT, environ: Mapping[str, str] = os.environ) -> str | None:
    """A pinned Jev model version (e.g. JEV_MODEL=jev-1.13.0), or None to use the client's default alias.

    TypeSafe's docs: aliases such as `jev-latest` "update automatically with new releases, so pinning specific version
    IDs is recommended" when results are compared over time. Pinning is opt-in, because a pinned version can eventually
    be retired; every stored row records the versioned id that actually answered either way."""
    try:
        return _find_key(("JEV_MODEL",), root, environ, "")[0]
    except ConfigError:
        return None
