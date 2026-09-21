"""The reader's interest profile: one human-edited TOML file, compiled into the shapes each consumer needs.

One source of truth, several renderings (a design rule from the Jev investigation):
  * `jev_state()`  a trimmed JSON object for Jev's `state`. Jev's docs warn that accuracy falls as unrelated
                   detail grows ("context rot"), and the parts of the profile a question points at are sent with EVERY item, so anything that does not
                   help a relevance decision is left out: personal identifiers, prose about character, current
                   projects, and the learning-style section.
  * `llm_text()`   readable text for the editor prompt (the Stage 2 model writes "why it matters to you", so it gets
                   the reader's stage and orientation too, but never the reader's name).
  * `hash`         a short content hash stored with every triage result, so any score can be traced to the exact
                   profile that produced it (the same idea as `prompt_version`). It hashes the PARSED data, so
                   editing comments or whitespace does not change it.

The loader is permissive about the shape of the file: sections it does not know are passed through, so the file
can grow. It rejects a file with none of the sections that describe interests.
"""

import copy
import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Sections that describe what the reader cares about. A file with none of them is not a usable profile.
DECISION_SECTIONS = (
    "core_interest_areas",
    "technical_interests",
    "research_interests",
    "building_interests",
    "high_value_information",
    "low_value_information",
    "discovery",
    "priority_hierarchy",
    "relevance_guidance",
)
_NOT_SENT_TO_JEV = ("profile", "learning_orientation")  # personal/meta, and not a relevance signal
_META_KEYS = ("name", "purpose", "version", "design_principle")  # of [profile]: never rendered for any model


class ProfileError(Exception):
    """The interest profile is missing or unusable."""


def approx_tokens(text: str) -> int:
    """A rough estimate (about 4 characters per token). Good enough to watch the profile's size."""
    return len(text) // 4


def _label(key: str) -> str:
    return key.replace("_", " ")


def _render(value, depth: int = 0) -> list[str]:
    """Render parsed TOML as compact, readable text. Deterministic: file order is preserved."""
    pad = "  " * depth
    lines: list[str] = []
    if isinstance(value, str):
        return [pad + value]
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                title = entry.get("name", "")
                priority = f" [{entry['priority']}]" if "priority" in entry else ""
                lines.append(f"{pad}### {title}{priority}".rstrip())
                rest = {k: v for k, v in entry.items() if k not in ("name", "priority")}
                lines += _render(rest, depth)
            else:
                lines.append(f"{pad}- {entry}")
        return lines
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                lines.append(f"{pad}{_label(key)}: {item}")
            elif isinstance(item, list) and item and all(isinstance(x, dict) for x in item):
                lines += _render(item, depth)
            elif isinstance(item, (list, dict)):
                lines.append(f"{pad}{_label(key)}:")
                lines += _render(item, depth + 1)
            else:
                lines.append(f"{pad}{_label(key)}: {item}")
        return lines
    return [f"{pad}{value}"]


@dataclass(frozen=True)
class Profile:
    data: dict
    hash: str

    def jev_state(self) -> dict:
        """The trimmed profile for Jev's `state` (see the module docstring for what is left out)."""
        state = copy.deepcopy(self.data)
        for key in _NOT_SENT_TO_JEV:
            state.pop(key, None)
        areas = state.pop("core_interest_areas", {}).get("area", [])
        building = state.get("building_interests")
        if isinstance(building, dict):
            building.pop("current_building_evidence", None)
        cleaned = [{k: v for k, v in area.items() if k != "interest_character"} for area in areas]
        return {"interest_areas": cleaned, **state} if cleaned else state

    def llm_text(self) -> str:
        """The whole profile as text for the editor prompt (everything except the reader's name and file metadata)."""
        lines: list[str] = []
        meta = self.data.get("profile", {})
        if meta.get("user_stage"):
            lines.append(f"Reader: {meta['user_stage']}")
        if meta.get("core_orientation"):
            lines.append(f"Orientation: {meta['core_orientation']}")
        for key, value in self.data.items():
            if key == "profile":
                continue
            section = value.get("area", value) if key == "core_interest_areas" and isinstance(value, dict) else value
            lines += ["", f"## {_label(key).title()}"] + _render(section)
        return "\n".join(lines).strip()


def load_profile(path: str | Path) -> Profile:
    file = Path(path)
    if not file.is_file():
        raise ProfileError(
            f"No interest profile at {file}. Copy config/interests.example.toml to config/interests.toml and edit it."
        )
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{file.name} is not valid TOML: {exc}") from exc
    if not any(section in data for section in DECISION_SECTIONS):
        raise ProfileError(
            f"{file.name} has none of the expected sections ({', '.join(DECISION_SECTIONS)}). "
            "See config/interests.example.toml."
        )
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return Profile(data=data, hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12])
