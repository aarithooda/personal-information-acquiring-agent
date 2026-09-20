"""Fault injection: break sources and the LLM in every way we handle (and some we don't), and
check that the database invariants hold after every single run, crashed or not.

The claim under test: you can kill the app at any moment, or have any dependency misbehave,
and no item is lost, none is shown twice, and the checkpoint never lies.
"""

import random
from datetime import timedelta

import pytest
from fakes import T0, FakeSource, SmartLLM, make_item

from pia.briefing.curate import EnrichmentFailed, make_curator
from pia.db import connect
from pia.http import SourceError
from pia.llm.client import LLMBadOutput, LLMUnavailable
from pia.pipeline import AllSourcesFailed, run_briefing
from pia.state import get_checkpoint

DAY = timedelta(days=1)
HANDLED = (AllSourcesFailed, EnrichmentFailed)  # runs that end cleanly with "nothing recorded"


class Crash(Exception):
    """Stands in for the process dying: nothing in our code catches this."""


class ChaosLLM:
    """Delegates to a well-behaved fake, but randomly fails the way real LLM APIs do."""

    def __init__(self, rng, unavailable=0.15, bad_output=0.15, crash=0.05):
        self.rng, self.rates = rng, (unavailable, bad_output, crash)
        self.inner = SmartLLM()

    def complete_json(self, **kwargs):
        roll = self.rng.random()
        unavailable, bad_output, crash = self.rates
        if roll < unavailable:
            raise LLMUnavailable("chaos: rate limited")
        if roll < unavailable + bad_output:
            raise LLMBadOutput("chaos: garbage")
        if roll < unavailable + bad_output + crash:
            raise Crash("chaos: process killed")
        return self.inner.complete_json(**kwargs)


class CrashOnCall:
    """Kills the 'process' on the k-th LLM call; otherwise behaves."""

    def __init__(self, k):
        self.k, self.calls, self.inner = k, 0, SmartLLM()

    def complete_json(self, **kwargs):
        self.calls += 1
        if self.calls == self.k:
            raise Crash(f"killed during LLM call {self.k}")
        return self.inner.complete_json(**kwargs)


def make_universe(per_source=40):
    """Items spread over ~12 days, so later runs find genuinely new ones."""
    return {
        name: [
            make_item(name, i, T0 - DAY + timedelta(days=12) * (i / per_source), content=f"Text {name}{i}")
            for i in range(per_source)
        ]
        for name in ("a", "b")
    }


def assert_invariants(conn, successful_runs):
    bad = conn.execute(
        """SELECT COUNT(*) FROM items
           WHERE (status IN ('briefed','skipped')) != (briefing_id IS NOT NULL)"""
    ).fetchone()[0]
    assert bad == 0, "an item's status and briefing link disagree"

    stray = conn.execute(
        """SELECT COUNT(*) FROM enrichments e JOIN items i ON i.id = e.item_id
           WHERE i.status IN ('discovered', 'failed')"""
    ).fetchone()[0]
    assert stray == 0, "an enrichment exists for an item that is not marked as triaged"

    unsaved = conn.execute(
        """SELECT COUNT(*) FROM items i
           WHERE i.status IN ('enriched','briefed','skipped')
             AND NOT EXISTS (SELECT 1 FROM enrichments e WHERE e.item_id = i.id)"""
    ).fetchone()[0]
    assert unsaved == 0, "an item is marked triaged but its triage result was lost"

    assert conn.execute("SELECT COUNT(*) FROM briefings").fetchone()[0] == len(successful_runs)
    expected = max(successful_runs) if successful_runs else None
    assert get_checkpoint(conn) == expected, "the checkpoint does not match the last committed briefing"


def run_once(conn, sources, llm, now):
    """One 'open the app'. Returns True if a briefing was committed."""
    try:
        run_briefing(conn, None, sources, now, prepare=make_curator(llm))
        return True
    except HANDLED + (Crash,):
        return False


@pytest.mark.parametrize("seed", range(25))
def test_random_failures_never_corrupt_state_and_a_healthy_run_recovers_everything(seed):
    rng = random.Random(seed)
    conn = connect(":memory:")
    universe = make_universe()
    healthy = [FakeSource(name, items) for name, items in universe.items()]

    class FlakySource(FakeSource):
        def fetch(self, client, since, until):
            if rng.random() < 0.25:
                raise SourceError("chaos: source down")
            return super().fetch(client, since, until)

    flaky = [FlakySource(name, items) for name, items in universe.items()]
    llm = ChaosLLM(rng)

    successes = []
    for k in range(8):  # eight sessions of opening the app, 1.5 days apart, with things breaking
        now = T0 + k * 1.5 * DAY
        if run_once(conn, flaky, llm, now):
            successes.append(now)
        assert_invariants(conn, successes)

    # The user finally opens the app on a good day: everything must settle.
    final = T0 + 12 * DAY
    for extra in range(3):
        if run_once(conn, healthy, SmartLLM(), final + extra * timedelta(hours=1)):
            successes.append(final + extra * timedelta(hours=1))
        assert_invariants(conn, successes)

    stuck = conn.execute("SELECT status, COUNT(*) FROM items WHERE status IN ('discovered','enriched') GROUP BY status").fetchall()
    assert stuck == [], f"items stuck in limbo: {[tuple(r) for r in stuck]}"

    stored = {r["external_id"] + r["source"] for r in conn.execute("SELECT source, external_id FROM items")}
    expected = {str(i) + name for name, items in universe.items() for i in range(len(items))}
    assert stored == expected, f"{len(expected - stored)} items were lost"


@pytest.mark.parametrize("kill_at", range(1, 9))
def test_killing_the_process_at_any_llm_call_loses_nothing(kill_at):
    conn = connect(":memory:")
    source = FakeSource("a", [make_item("a", i, T0 - timedelta(hours=1), content="Text") for i in range(25)])

    crashed = not run_once(conn, [source], CrashOnCall(kill_at), T0)
    assert_invariants(conn, [] if crashed else [T0])

    # "Restart the app": a normal run must finish the job, exactly once per item.
    assert run_once(conn, [source], SmartLLM(), T0 + timedelta(hours=1))
    assert conn.execute("SELECT COUNT(*) FROM items WHERE status IN ('discovered','enriched')").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 25
    shown = conn.execute("SELECT COUNT(*) FROM items WHERE status = 'briefed'").fetchone()[0]
    assert shown > 0
