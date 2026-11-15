"""Model routing and the cost meter.

`ModelConfig` says which model does which task and what it costs (config/models.toml). `Meter` wraps any `LLM`
so every call is timed and its token usage recorded; records are buffered in memory and written with `flush`,
because the wrapped client may be used from a thread that does not own the database connection.

Keeping this in one place is what makes providers replaceable: a new provider is one class implementing
`complete_json`, one `[prices]` entry and one line in `[tasks]`.
"""

import sqlite3
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from pia.config import PROJECT_ROOT
from pia.llm.client import LLM, LLMError
from pia.store.db import iso, utcnow

MODELS_FILE = PROJECT_ROOT / "config" / "models.toml"

DEFAULT_TASKS = {"assess": "openai/gpt-oss-20b", "brief": "openai/gpt-oss-120b", "extract": "openai/gpt-oss-120b", "source_eval": "openai/gpt-oss-120b"}
DEFAULT_PROVIDERS = {"jev": False}  # Groq is the only external model provider unless this is switched on
DEFAULT_BUDGET = {"assess_items_per_cycle": 120, "briefs_per_cycle": 12, "fit_items_per_cycle": 30, "daily_usd": 1.0}


@dataclass
class ModelConfig:
    tasks: dict = field(default_factory=lambda: dict(DEFAULT_TASKS))
    budget: dict = field(default_factory=lambda: dict(DEFAULT_BUDGET))
    prices: dict = field(default_factory=dict)
    providers: dict = field(default_factory=lambda: dict(DEFAULT_PROVIDERS))

    @classmethod
    def load(cls, path: Path = MODELS_FILE) -> "ModelConfig":
        config = cls()
        if Path(path).is_file():
            with open(path, "rb") as handle:
                data = tomllib.load(handle)
            config.tasks.update(data.get("tasks", {}))
            config.budget.update(data.get("budget", {}))
            config.prices = data.get("prices", {})
            config.providers.update(data.get("providers", {}))
        return config

    @property
    def jev_enabled(self) -> bool:
        """Whether the dormant Jev integration may be used at all. Off unless config/models.toml turns it on;
        the presence of a Jev key in the environment is NOT enough. When off, PIA makes no Jev call and ranking
        ignores any Jev judgement stored earlier."""
        return self.providers.get("jev") is True

    def model_for(self, task: str) -> str:
        return self.tasks[task]

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self.prices.get(model) or (self.prices.get("jev") if model.startswith("jev") else None)
        if not price:
            return 0.0
        return (input_tokens * price.get("input", 0) + output_tokens * price.get("output", 0)) / 1_000_000

    def provider(self, model: str) -> str:
        price = self.prices.get(model) or (self.prices.get("jev") if model.startswith("jev") else None)
        return (price or {}).get("provider", "groq")


@dataclass
class UsageRecord:
    at: datetime
    provider: str
    model: str
    task: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    items: int = 0
    latency_ms: int = 0
    ok: bool = True
    error: str | None = None


class Meter:
    """An `LLM` that records what it spends. `task` and `items` are set by the caller for each job."""

    def __init__(self, llm: LLM, config: ModelConfig, *, clock=utcnow):
        self._llm = llm
        self.config = config
        self._clock = clock
        self.records: list[UsageRecord] = []
        self.task = "unknown"
        self.items = 0

    def for_task(self, task: str, items: int = 1) -> "Meter":
        self.task, self.items = task, items
        return self

    def complete_json(self, **kwargs) -> dict:
        model = kwargs["model"]
        record = UsageRecord(at=self._clock(), provider=self.config.provider(model), model=model, task=self.task, items=self.items)
        started = time.perf_counter()
        try:
            result = self._llm.complete_json(**kwargs)
        except LLMError as exc:
            record.ok, record.error = False, str(exc)[:300]
            raise
        finally:
            record.latency_ms = int((time.perf_counter() - started) * 1000)
            usage = getattr(self._llm, "last_usage", None) if record.ok else None
            if usage:
                record.input_tokens, record.output_tokens = usage["input_tokens"], usage["output_tokens"]
                record.cost_usd = self.config.cost(model, record.input_tokens, record.output_tokens)
            self.records.append(record)
        return result

    def add(self, record: UsageRecord) -> None:
        """For providers that are not an `LLM` (Jev): the caller builds the record."""
        self.records.append(record)

    def flush(self, conn: sqlite3.Connection) -> None:
        if not self.records:
            return
        conn.executemany(
            """INSERT INTO llm_usage (at, provider, model, task, input_tokens, output_tokens, cost_usd, items, latency_ms, ok, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(iso(r.at), r.provider, r.model, r.task, r.input_tokens, r.output_tokens, r.cost_usd, r.items, r.latency_ms, int(r.ok), r.error) for r in self.records],
        )
        conn.commit()
        self.records = []


def spent_today(conn: sqlite3.Connection, now: datetime) -> float:
    start = iso(now.replace(hour=0, minute=0, second=0, microsecond=0))
    return conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE at >= ?", (start,)).fetchone()[0]


def usage_summary(conn: sqlite3.Connection, now: datetime, days: int = 30) -> dict:
    since = iso(now.replace(hour=0, minute=0, second=0, microsecond=0)) if days == 0 else iso(now - timedelta(days=days))
    by_task = [dict(row) for row in conn.execute(
        """SELECT task, model, COUNT(*) AS calls, SUM(items) AS items, SUM(input_tokens) AS input_tokens,
                  SUM(output_tokens) AS output_tokens, ROUND(SUM(cost_usd), 5) AS cost_usd, SUM(1 - ok) AS failures
           FROM llm_usage WHERE at >= ? GROUP BY task, model ORDER BY cost_usd DESC""", (since,))]
    by_day = [dict(row) for row in conn.execute(
        "SELECT substr(at, 1, 10) AS day, ROUND(SUM(cost_usd), 5) AS cost_usd, COUNT(*) AS calls FROM llm_usage WHERE at >= ? GROUP BY day ORDER BY day", (since,))]
    return {"days": days, "total_usd": round(sum(row["cost_usd"] or 0 for row in by_task), 5), "today_usd": round(spent_today(conn, now), 5), "by_task": by_task, "by_day": by_day}
