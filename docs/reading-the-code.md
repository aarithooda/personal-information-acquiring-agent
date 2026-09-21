# Reading the code

PIA is also a learning project: the code is small and readable on purpose, and [design.md](design.md) records why each
significant decision was made, what the alternatives were, and what would change later.

## A suggested order

1. `models.py`, `normalize.py`, `db.py`: the data shape, item identity, the schema and its migrations
2. `state.py`, `collect.py`, `pipeline.py`: checkpoints, cursors, and the fixed sequence of steps
3. `sources/base.py` and one adapter such as `sources/hackernews.py`: parse (pure) vs fetch (network)
4. `http.py`: the single place that retries
5. `llm/client.py`, `llm/prompts.py`, `llm/enrich.py`: structured output, untrusted input, batching, failure
6. `briefing/rank.py`, `briefing/budget.py`, `llm/headlines.py`, `briefing/curate.py`: from scores to a briefing
7. `profile.py`, `jev/client.py`, `jev/design.py`, `jev/triage.py`: one profile compiled for several consumers, typed questions in and validated answers out, and combining answers in code
8. `history.py`, `doctor.py`, `cli.py`: read-only access and the command line
9. `web/`: `favorites.py` and `queries.py` (data), `briefing_view.py` (parsing saved briefings), `app.py` (the API), `static/` (the page)
10. `tests/`: `fakes.py` (a scriptable LLM), `test_pipeline.py` (scenarios), `test_resilience.py` (fault injection)

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
| API as a contract (typed responses) | `web/schemas.py`, `/docs` |
| Idempotency in HTTP (`PUT`/`DELETE`, not a toggle) | `web/app.py`, `web/favorites.py` |
| A read model, guarded by a contract test | `web/briefing_view.py`, `tests/test_briefing_view.py` |
| Threat model of a local server (Host check, CSP, no CORS) | `web/app.py` |
| Untrusted text and XSS (text nodes only) | `web/static/app.js` |
| Pure logic split from the DOM so it can be tested | `web/static/logic.js`, `tests/test_web_logic.py` |
| SQLite connections belong to one thread | `web/app.py` |
| Pre-registered evaluation: freeze, blind labels, one run | `benchmark_v2/METHODOLOGY.md`, `benchmark_v2/freeze.py` |
