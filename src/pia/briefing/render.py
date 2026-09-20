"""Deterministic rendering of a briefing to markdown.

M3 renders a plain grouped list. That wall of links is on purpose: it is the
"before" picture that the LLM stages (M4/M5) exist to compress.
Times are shown in UTC so the output is identical wherever it is generated.
"""

import sqlite3
from datetime import datetime
from itertools import groupby

from pia.collect import SourceResult


def _stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC")


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
