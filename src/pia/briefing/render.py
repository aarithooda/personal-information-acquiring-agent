"""Deterministic rendering of a briefing to markdown.

M3 renders a plain grouped list. That wall of links is on purpose: it is the
"before" picture that the LLM stages (M4/M5) exist to compress.
Times are shown in UTC so the output is identical wherever it is generated.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from itertools import groupby

from pia.briefing.content import BriefingContent
from pia.collect import SourceResult
from pia.state import pending_items


def _stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC")


SECTIONS = (("ai", "🤖 AI & Agents"), ("software", "💻 Software"), ("research", "🔬 Research"))


def _ago(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return "less than an hour"
    if hours < 48:
        return f"{round(hours)} hour{'s' if round(hours) != 1 else ''}"
    return f"{round(hours / 24)} days"


def render_briefing(
    *,
    headlines: list,
    extras: list[sqlite3.Row],
    checkpoint: datetime | None,
    now: datetime,
    results: list[SourceResult],
    triaged: int,
    awaiting: int,
    editor_failed: bool,
) -> str:
    """The briefing layout. `headlines` are llm.headlines.Headline objects."""
    lines = ["# Since you last checked", ""]
    if checkpoint:
        lines.append(f"Last checked {_stamp(checkpoint)} ({_ago(now - checkpoint)} ago). Skimmed {triaged} new items.")
    else:
        lines.append(f"First run. Skimmed {triaged} items from the last few days.")
    lines.append("")

    notes = [f"{r.name} was unavailable ({r.error}); it will catch up next time." for r in results if not r.ok]
    if editor_failed:
        notes.append("The editor step was unavailable, so these headlines were ranked automatically and have no 'why it matters' notes.")
    if awaiting:
        notes.append(f"{awaiting} items could not be processed yet and will be included next time.")
    lines += [f"> ⚠ {note}" for note in notes] + ([""] if notes else [])

    if not headlines and not extras:
        lines.append("Nothing new since your last check." if not triaged else "Nothing stood out in the new items.")
        return "\n".join(lines) + "\n"

    if headlines:
        lines += ["## 🔥 Worth knowing", ""]
        for headline in headlines:
            row = headline.row
            lines += [f"### [{row['title']}]({row['url']})", "", headline.explanation, ""]
            if headline.why_it_matters:
                lines += [f"**Why it matters:** {headline.why_it_matters}", ""]
            lines += [f"*via {', '.join(sorted(json.loads(row['signals'])))}*", ""]

    for category, heading in SECTIONS:
        rows = [row for row in extras if row["category"] == category]
        if rows:
            lines += [f"## {heading}", ""]
            # An empty summary means the item had no text to summarize: show the link, invent nothing.
            lines += [
                f"- [{row['title']}]({row['url']})" + (f": {row['summary']}" if row["summary"] else "")
                for row in rows
            ]
            lines.append("")

    hidden = triaged - len(headlines) - len(extras)
    if hidden > 0:
        lines += ["---", f"*{hidden} lower-priority items were skipped.*"]
    return "\n".join(lines) + "\n"


def plain_prepare(conn, checkpoint, now, results) -> BriefingContent:
    """No-LLM briefing: every new item, listed. Kept as the simplest possible 'prepare'."""
    items = pending_items(conn)
    return BriefingContent(render_plain(items, checkpoint, now, results), shown=items)


def render_plain(
    items: list[sqlite3.Row],
    checkpoint: datetime | None,
    now: datetime,
    results: list[SourceResult],
) -> str:
    lines = ["# Since you last checked", ""]
    if checkpoint:
        lines.append(f"Last checked {_stamp(checkpoint)}. Now {_stamp(now)}.")
    else:
        lines.append(f"First run. Now {_stamp(now)}.")
    lines += ["", f"**{len(items)} new item(s).**", ""]

    failed = [r for r in results if not r.ok]
    if failed:
        lines.append("Some sources were unavailable and will catch up next time:")
        lines += [f"- {r.name}: {r.error}" for r in failed]
        lines.append("")

    by_source = sorted(items, key=lambda row: row["source"])
    for source, group in groupby(by_source, key=lambda row: row["source"]):
        rows = list(group)
        lines += [f"## {source} ({len(rows)})", ""]
        for row in rows:
            published = (row["published_at"] or row["discovered_at"])[:10]
            lines.append(f"- [{row['title']}]({row['url']}) ({published})")
        lines.append("")
    return "\n".join(lines)
