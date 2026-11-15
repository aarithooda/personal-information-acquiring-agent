# Reading the code

PIA is also a learning project: the code is small and readable on purpose, and [design.md](design.md) records why each
significant decision was made, what the alternatives were, and what would change later.

## The platform (the product)

Read [platform/ARCHITECTURE.md](platform/ARCHITECTURE.md) first: it explains the shape and every decision. Then, in order:

1. `store/schema.py`, `store/db.py`, `store/seed.py`: the data model, migrations, and how config files become rows
2. `acquire/runner.py`, `acquire/adapters.py`, `acquire/feeds.py`: fetching, cursors, failure handling, and each kind of source
3. `acquire/safefetch.py`, `acquire/textextract.py`: the guarded fetcher and dependency-free HTML reading
4. `intel/classify.py`, `intel/score.py`: everything decided without a model
5. `intel/usage.py`, `intel/assess.py`, `intel/brief.py`, `intel/opportunities.py`: where models are called, and how their output is checked
6. `intel/relate.py`, `intel/learn.py`: connected items, and learning from behaviour
7. `notify/engine.py`, `worker.py`: notifications and the cycle that ties everything together
8. `store/queries.py`, `server/app.py`, `server/auth.py`: the read models, the API and its security rules
9. `server/static/js/`: `util.js` (pure logic), `ui.js` (components), `app.js` (shell, Home, feeds), `item.js` (reader), `pages.js`
10. `tests/platform_helpers.py` and `tests/test_platform_*.py`

All paths are under `src/pia/` except `tests/`.

## The v1 briefing pipeline (frozen)

A suggested order:

1. `models.py`, `normalize.py`, `db.py`: the data shape, item identity, the schema and its migrations
2. `state.py`, `collect.py`, `pipeline.py`: checkpoints, cursors, and the fixed sequence of steps
3. `sources/base.py` and one adapter such as `sources/hackernews.py`: parse (pure) vs fetch (network)
4. `http.py`: the single place that retries
5. `llm/client.py`, `llm/prompts.py`, `llm/enrich.py`: structured output, untrusted input, batching, failure
6. `briefing/rank.py`, `briefing/budget.py`, `llm/headlines.py`, `briefing/curate.py`: from scores to a briefing
7. `profile.py`, `jev/client.py`, `jev/design.py`, `jev/triage.py`: one profile compiled for several consumers, typed questions in and validated answers out, and combining answers in code
8. `history.py`, `doctor.py`, `cli.py`: read-only access and the command line
9. `tests/`: `fakes.py` (a scriptable LLM), `test_pipeline.py` (scenarios), `test_resilience.py` (fault injection)

All paths are under `src/pia/` except `tests/`.

## Concepts, and where to see them

| Concept | Where to see it |
|---|---|
| Deterministic orchestration vs agents | `pipeline.py` |
| Identity and dedup by database constraint | `normalize.py`, `db.py` |
| Idempotency and checkpointing | `collect.py`, `state.py` |
| Injecting the clock (`now` is a parameter) | everywhere; see the tests |
| Structured LLM output, and validating it anyway | `llm/client.py`, `llm/enrich.py` |
| Untrusted input and prompt injection | `llm/prompts.py` |
| Pointwise vs listwise judgment | `llm/enrich.py` vs `llm/headlines.py` |
| Graceful degradation | `briefing/curate.py` |
| Error taxonomy and batch bisection | `llm/client.py`, `llm/enrich.py` |
| Read-only access | `history.py` |
| Fault injection | `tests/test_resilience.py` |
| A decision model versus a generalist LLM | `jev/client.py`, `jev/triage.py` |
| Decompose a judgment into atomic questions, combine in code | `jev/design.py` (`plan`, `derive`) |
| Store raw model output so the formula can change without new calls | `jev/triage.py`, `enrichments.details` |
| One source of truth, several renderings (profile to JSON and to text) | `profile.py` |
| Backpressure for a bounded worker pool | `jev/triage.py` (`triage_pending`) |
| Request bodies as typed models | `server/app.py` |
| Idempotent state changes (set, not toggle) | `intel/learn.py` (`set_item_state`), `server/app.py` |
| Read models kept apart from the HTTP layer | `store/queries.py` |
| Threat model of a local server (Host check, CSP, Origin check) | `server/app.py`, `tests/test_platform_api.py` |
| Untrusted text and XSS (text nodes only, checked links) | `server/static/js/ui.js`, `util.js` (`safeUrl`) |
| Pure logic split from the DOM so it can be tested | `server/static/js/util.js`, `tests/test_platform_misc.py` |
| SQLite connections belong to one thread | `server/app.py`, `worker.py` |
| Server-side request forgery, and a guarded fetcher | `acquire/safefetch.py` |
| Raw model output stored apart from derived scores | `intel/assess.py`, `intel/score.py` |
| Pre-registered evaluation: freeze, blind labels, one run | `benchmark_v2/METHODOLOGY.md`, `benchmark_v2/freeze.py` |
