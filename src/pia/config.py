"""Where things live. Everything is relative to the project root, not the current directory."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "pia.db"
DEFAULT_SOURCES = PROJECT_ROOT / "config" / "sources.toml"
