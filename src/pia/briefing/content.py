"""What a 'prepare' step hands back to the pipeline."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from pia.collect import SourceResult


@dataclass
class BriefingContent:
    markdown: str
    shown: list[sqlite3.Row]  # items the user is being briefed on
    skipped: list[sqlite3.Row] = field(default_factory=list)  # considered, deliberately not shown


# prepare(conn, checkpoint, now, source_results) -> BriefingContent
# It may raise; nothing has been committed at that point.
Prepare = Callable[[sqlite3.Connection, datetime | None, datetime, list[SourceResult]], BriefingContent]
