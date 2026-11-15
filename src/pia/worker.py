"""The agent's loop: one cycle of acquire -> classify -> assess -> extract -> fit -> rank -> relate -> notify,
and the background thread that repeats it.

Each step is isolated: a step that fails is recorded in the run's stats and the cycle carries on, so a model
outage never stops acquisition and a broken source never stops ranking. The cycle is safe to run at any time
and from several entry points (`pia serve`'s thread, `pia run-once` under cron, the "Run now" button).
"""

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from pia.acquire import discovery
from pia.acquire.runner import acquire
from pia.config import DEFAULT_PROFILE, ConfigError, get_groq_api_key, get_jev_api_key, get_jev_model
from pia.http import USER_AGENT
from pia.intel import jevfit, learn, opportunities, reader
from pia.intel.assess import assess_pending
from pia.intel.brief import write_brief
from pia.intel.classify import Lexicon, classify_new
from pia.intel.relate import relate_pending
from pia.intel.score import refresh_rank, rescore
from pia.intel.usage import Meter, ModelConfig, spent_today
from pia.llm.client import GroqClient, LLMError
from pia.notify import engine as notify
from pia.profile import Profile, ProfileError, load_profile
from pia.store import db, queries

log = logging.getLogger(__name__)

CYCLE_SECONDS = 600
DISCOVERY_EVERY = timedelta(hours=24)
BACKUP_EVERY = timedelta(hours=24)


@dataclass
class Services:
    """Everything that reaches outside the process. Any of the model services may be absent: PIA then runs on
    deterministic processing alone and says so in `notes`."""

    http: httpx.Client
    config: ModelConfig
    llm: Meter | None = None
    jev: object | None = None
    profile: Profile | None = None
    notes: list[str] = field(default_factory=list)
    groq_key: str | None = None

    def new_meter(self) -> Meter | None:
        """A fresh, independent model client for one request thread (the worker's own is not shared)."""
        return Meter(GroqClient(self.groq_key, self.http), self.config) if self.groq_key else None

    def close(self) -> None:
        self.http.close()


def build_services(environ=os.environ) -> Services:
    http = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=45, follow_redirects=True)
    services = Services(http=http, config=ModelConfig.load())
    if environ.get("PIA_NO_MODELS"):
        services.notes.append("Models are switched off (PIA_NO_MODELS).")
        return services
    try:
        services.groq_key = get_groq_api_key()
        services.llm = services.new_meter()
    except ConfigError:
        services.notes.append("No GROQ_API_KEY: summaries, briefs and model assessment are off; ranking uses deterministic signals.")
    if not services.config.jev_enabled:
        # Groq-only operation (the default). The Jev key is not even read, so nothing can reach TypeSafe's API.
        return services
    try:
        from pia.jev.client import DEFAULT_MODEL, JevClient

        key = get_jev_api_key()
        services.profile = load_profile(DEFAULT_PROFILE)
        services.jev = JevClient(key, http, model=get_jev_model() or DEFAULT_MODEL)
    except ConfigError:
        services.notes.append("Jev is switched on in config/models.toml but no Jev key was found: the personal-fit judgement is off.")
    except ProfileError:
        services.notes.append("Jev is switched on but config/interests.toml is missing: the personal-fit judgement is off.")
    return services


def _step(stats: dict, name: str, action):
    try:
        stats[name] = action()
    except Exception as exc:  # noqa: BLE001 - one failing step must not end the cycle
        log.exception("cycle step %s failed", name)
        stats.setdefault("errors", {})[name] = f"{exc.__class__.__name__}: {exc}"[:300]


def _due(conn: sqlite3.Connection, key: str, every: timedelta, now: datetime) -> bool:
    last = db.parse_ts(db.get_setting(conn, key))
    return last is None or now - last >= every


def run_cycle(conn: sqlite3.Connection, services: Services, now: datetime, *, db_path: str | Path | None = None,
              fetch: bool = True, force: bool = False, only: list[int] | None = None, models: bool = True) -> dict:
    """One full pass. Returns the stats that are stored with the run."""
    run_id = db.start_run(conn, "cycle", now)
    stats: dict = {}
    config = services.config
    llm = services.llm if models else None
    over_budget = spent_today(conn, now) >= float(config.budget["daily_usd"])
    if over_budget and llm is not None:
        stats["budget"] = "daily model budget reached; model steps skipped"
        llm = None

    if fetch:
        def do_acquire():
            results = acquire(conn, services.http, now, only=only, force=force)
            return {"sources": len(results), "failed": sum(1 for r in results if not r.ok), "new": sum(r.inserted for r in results),
                    "merged": sum(r.merged for r in results), "errors": {r.name: r.error for r in results if not r.ok}}
        _step(stats, "acquire", do_acquire)

    lexicon = Lexicon.load()
    _step(stats, "classified", lambda: classify_new(conn, lexicon))
    _step(stats, "prescored", lambda: rescore(conn, now, item_ids=[row["id"] for row in conn.execute(
        "SELECT id FROM items WHERE stage = 'classified' AND model_importance IS NULL AND score = 0")]))

    if llm is not None:
        def do_assess():
            report = assess_pending(conn, llm, config.model_for("assess"), now, limit=int(config.budget["assess_items_per_cycle"]))
            return {"assessed": report.assessed, "failed_items": report.failed_items, "failed_batches": report.failed_batches,
                    "remaining": report.remaining, "stopped_early": report.stopped_early}
        _step(stats, "assess", do_assess)

    def do_opportunities():
        done = {"model": 0, "rules": 0}
        for item_id in opportunities.pending_extraction(conn):
            content = conn.execute("SELECT LENGTH(COALESCE(content, '')) FROM items WHERE id = ?", (item_id,)).fetchone()[0]
            if content < 400:  # a bare link: read the posting itself before extracting from it
                reader.fetch_text(conn, services.http, item_id, now)
            used = opportunities.extract_opportunity(conn, item_id, now, llm=llm, model=config.model_for("extract"))
            done[used] = done.get(used, 0) + 1
        return done
    _step(stats, "opportunities", do_opportunities)

    # Dormant by default: runs only if config/models.toml switches Jev on AND a client was built (see build_services).
    if config.jev_enabled and services.jev is not None and services.profile is not None and models and not over_budget:
        _step(stats, "fit", lambda: jevfit.judge_shortlist(conn, services.jev, services.profile, services.llm, now, limit=int(config.budget["fit_items_per_cycle"])))

    _step(stats, "rescored", lambda: rescore(conn, now))
    _step(stats, "relations", lambda: relate_pending(conn, now))
    _step(stats, "ranked", lambda: refresh_rank(conn, now))
    _step(stats, "notifications", lambda: len(notify.evaluate(conn, now)))

    if llm is not None:
        def do_briefs():
            written = 0
            for item in queries.featured(conn, now):
                if written >= int(config.budget["briefs_per_cycle"]):
                    break
                if conn.execute("SELECT 1 FROM briefs WHERE item_id = ?", (item["id"],)).fetchone():
                    continue
                try:
                    write_brief(conn, llm, config.model_for("brief"), item["id"], now)
                    written += 1
                except LLMError as exc:
                    log.warning("brief for item %s failed: %s", item["id"], exc)
                    break
            return written
        _step(stats, "briefs", do_briefs)

    if fetch and _due(conn, "discovery_last", DISCOVERY_EVERY, now):
        def do_discovery():
            found = discovery.discover_from_links(conn, services.http, now, llm=llm, model=config.model_for("source_eval"))
            db.set_setting(conn, "discovery_last", db.iso(now))
            conn.commit()
            return len(found)
        _step(stats, "discovered", do_discovery)

    _step(stats, "learning_decay", lambda: learn.decay_learning(conn, now))
    if db_path and str(db_path) != ":memory:" and _due(conn, "backup_last", BACKUP_EVERY, now):
        def do_backup():
            target = db.backup(conn, db_path, now)
            db.set_setting(conn, "backup_last", db.iso(now))
            conn.commit()
            return target.name
        _step(stats, "backup", do_backup)

    if services.llm is not None:
        services.llm.flush(conn)
    db.finish_run(conn, run_id, db.utcnow(), stats=stats, error="; ".join(f"{k}: {v}" for k, v in stats.get("errors", {}).items()) or None)
    return stats


class Worker(threading.Thread):
    """The background agent inside `pia serve`. It owns its own database connection (SQLite connections are
    bound to their thread) and can be woken early by `nudge()`."""

    def __init__(self, db_path: str | Path, services: Services, *, interval: int = CYCLE_SECONDS, clock=db.utcnow):
        super().__init__(name="pia-worker", daemon=True)
        self._db_path = db_path
        self._services = services
        self._interval = interval
        self._clock = clock
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._force = False
        self.running = False
        self.last_stats: dict = {}

    def nudge(self, *, force: bool = False) -> None:
        self._force = self._force or force
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        conn = db.connect(self._db_path)
        try:
            while not self._stop.is_set():
                force, self._force = self._force, False
                self._wake.clear()
                self.running = True
                try:
                    self.last_stats = run_cycle(conn, self._services, self._clock(), db_path=self._db_path, force=force)
                except Exception:  # noqa: BLE001 - the loop must survive anything a cycle throws
                    log.exception("worker cycle crashed")
                finally:
                    self.running = False
                self._wake.wait(self._interval)
        finally:
            conn.close()
