"""Turn a saved briefing's markdown back into structure, for display.

Why parse text at all: the editor's explanations exist only inside each briefing's stored markdown
(there is no table for them), and the briefings you already have cannot be regenerated. Parsing the
stored text is the only way to give *every* historical briefing a structured view without changing the core.

The price is coupling to the renderer's layout. It is kept honest two ways:
  * the parser reuses the renderer's own constants (SECTIONS), and
  * tests/test_briefing_view.py feeds it the REAL render_briefing output, so a layout change fails a test.
Anything it does not recognise returns None, and the UI falls back to showing the raw text.
"""

import re
from dataclasses import dataclass, field

from pia.briefing.render import SECTIONS

HEADLINES_HEADING = "🔥 Worth knowing"
_SECTION_BY_HEADING = {heading: category for category, heading in SECTIONS}
_EMPTY_MESSAGES = ("Nothing new since your last check.", "Nothing stood out in the new items.")

_TITLE_LINK = r"\[(.+)\]\((\S+?)\)"  # greedy title so titles containing brackets survive
_HEADLINE = re.compile(rf"^### {_TITLE_LINK}$")
_BULLET = re.compile(rf"^- {_TITLE_LINK}(?:: (.*))?$")
_VIA = re.compile(r"^\*via (.+)\*$")
_HIDDEN = re.compile(r"^\*(\d+) lower-priority items were skipped\.\*$")
_WHY_PREFIX = "**Why it matters:** "
_NOTE_PREFIX = "> ⚠ "


@dataclass
class ParsedHeadline:
    title: str
    url: str
    explanation: str
    why_it_matters: str | None
    via: list[str]


@dataclass
class ParsedExtra:
    title: str
    url: str
    summary: str | None


@dataclass
class ParsedBriefing:
    intro: str
    notes: list[str]
    headlines: list[ParsedHeadline]
    sections: list[tuple[str, list[ParsedExtra]]]  # (category, items), in the renderer's order
    hidden: int = 0
    empty_message: str | None = None


def parse_briefing(markdown: str) -> ParsedBriefing | None:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "# Since you last checked":
        return None

    intro, empty, hidden = "", None, 0
    notes: list[str] = []
    headlines: list[ParsedHeadline] = []
    sections: list[tuple[str, list[ParsedExtra]]] = []
    mode = "intro"
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current:
            headlines.append(
                ParsedHeadline(
                    current["title"],
                    current["url"],
                    "\n".join(current["body"]),
                    current["why"],
                    current["via"],
                )
            )
        current = None

    for raw in lines[1:]:
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith(_NOTE_PREFIX):
            notes.append(line[len(_NOTE_PREFIX) :])
            continue
        if line.startswith("## "):
            flush()
            heading = line[3:]
            if heading == HEADLINES_HEADING:
                mode = "headlines"
            elif heading in _SECTION_BY_HEADING:
                mode = "section"
                sections.append((_SECTION_BY_HEADING[heading], []))
            else:
                return None  # a heading we do not know: the layout changed
            continue
        if line == "---":
            flush()
            mode = "footer"
            continue

        if mode == "intro":
            if line in _EMPTY_MESSAGES:
                empty = line
            elif not intro:
                intro = line
            else:
                return None
        elif mode == "headlines":
            match = _HEADLINE.match(line)
            if match:
                flush()
                current = {"title": match.group(1), "url": match.group(2), "body": [], "why": None, "via": []}
            elif current is None:
                return None
            elif line.startswith(_WHY_PREFIX):
                current["why"] = line[len(_WHY_PREFIX) :] or None
            elif (via := _VIA.match(line)):
                current["via"] = [name.strip() for name in via.group(1).split(",")]
            else:
                current["body"].append(line)
        elif mode == "section":
            match = _BULLET.match(line)
            if not match:
                return None
            sections[-1][1].append(ParsedExtra(match.group(1), match.group(2), match.group(3) or None))
        elif mode == "footer":
            match = _HIDDEN.match(line)
            if not match:
                return None
            hidden = int(match.group(1))
    flush()

    if not headlines and not any(items for _, items in sections) and empty is None:
        return None
    return ParsedBriefing(intro, notes, headlines, sections, hidden, empty)
