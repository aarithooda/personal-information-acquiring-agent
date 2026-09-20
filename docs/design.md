# PIA V1 Design

Question V1 answers: **"What important things have happened since I last checked?"**

## Pipeline

```
sources (arxiv, hf papers, hn, github, rss)   one adapter per source
   -> RawItem[]                                (validated at the boundary)
   -> canonicalize URL -> upsert into SQLite   (deterministic dedup)
   -> select items not yet briefed             (state-based, not time-based)
   -> LLM stage 1: classify + score (batched, structured output)
   -> deterministic selection + grouping
   -> LLM stage 2: "why it matters" for the top few
   -> deterministic render (terminal + markdown file)
   -> commit briefing + checkpoint in ONE transaction
```

The pipeline is fixed program control flow, not an agent. The LLM makes judgments at two
points; it never touches timestamps, IDs, dedup or the checkpoint.

## Decisions

| # | Decision | Main alternative | Why for V1 | Revisit when |
|---|----------|------------------|------------|--------------|
| D1 | SQLite, plain SQL | Postgres / ORM | Single user, tiny data, real transactions and UNIQUE constraints | V3 needs vector search |
| D2 | APIs/RSS only | Scraping | Stable contracts vs. fragile HTML | A must-have source has no feed |
| D3 | Checkpoint = `briefings.covers_until`; per-source cursors in `fetch_runs`; "new" selected by `briefing_id IS NULL` | Single `last_checked` column | Crash-safe and idempotent; failed sources don't lose items | Multi-device / multi-user |
| D4 | Raw items and LLM enrichments in separate tables, tagged with model + prompt version | Columns on `items` | Enrichments are recomputable opinions, not facts | V2 re-scoring |
| D5 | Dedup by canonical URL + DB UNIQUE constraint; repeat sightings merge signals | Embeddings | Exact-match dedup needs no ML | V3 near-duplicate detection |
| D6 | Structured (schema-validated) LLM output, batched, cheap model for volume, stronger model only for top items; layout rendered by code | Free-form LLM briefing | Downstream code needs reliable fields; cost | Prompt/quality tuning |
| D7 | CLI first | Web app | Pipeline as a library function; UI can come later | V2+ |

## Schema notes

- All timestamps: UTC ISO-8601 text. Display converts to local time.
- `items.published_at` is NULL when the source gives none. We do not invent dates.
- `items.signals` is JSON namespaced by source: `{"hn": {"points": 120}}`. Flexible, not SQL-queryable.
- `items.source` is the *first* source to report the item; later sightings only add signals.
- A `briefings` row exists only if the briefing was fully committed, so existence = completion.
- Migrations: `PRAGMA user_version` plus an append-only list in `db.py`. Never edit a released one.
- `now` is passed into functions instead of read from the clock (deterministic, testable).

## Roadmap

M1 core (schema, normalize, dedup) -> M2 source adapters -> M3 checkpoint + new-items
query, no LLM -> M4 LLM enrichment -> M5 selection, stage 2, render -> M6 failure
handling -> M7 polish.

## Source notes (M2)

Verified live on 2026-09-20 (7-day window): arXiv, HF Daily Papers, HN (Algolia), GitHub
search, and RSS feeds for Quanta, DeepMind, OpenAI and the HF blog all work. About 310 items
per week in total, with a paper seen by three sources stored once.

Known limitations, accepted for V1:
- **arXiv is capped** at `max_results` (newest first) and 5 days of `cs.AI` alone is ~500
  papers. A capped fetch drops older papers in the window. M3 must decide how the per-source
  cursor behaves when a fetch was truncated. HF Daily Papers covers the important ones.
- **GitHub finds repos *created* in the window**, so an old repo that suddenly goes viral is missed.
- **Anthropic has no RSS feed**, so it is not a source yet.
- Adapters are `parse_*` (pure, tested on saved real responses in `tests/fixtures`) plus a
  thin `fetch`. All network access goes through `http.get_with_retry`.

## Checkpoint and catch-up rules (M3)

- **Per-source window:** `since = last successful fetch - 6h overlap` (first run: 3 days back;
  never more than 14 days back), `until = now`. Overlap absorbs late-indexed items; dedup makes it free.
- **"New" is state, not time:** items with `briefing_id IS NULL`. Publication date is irrelevant.
- **Write order:** store items, then advance the source cursor; a crash between them only causes a harmless re-fetch.
- **One transaction** creates the briefing row (= the checkpoint) and marks its items briefed.
- **Failures:** a failed source keeps its old cursor and catches up later; the briefing names it.
  If *every* source fails, nothing is committed and the checkpoint does not move.
- **Truncated fetches (arXiv cap):** decision for V1 is that the cursor still advances. The cap is
  deliberate sampling (newest N), not a promise of completeness. Revisit in V2 when interest
  matching can sift arXiv by topic instead of by recency.
- Times are rendered in UTC. `pia status` shows the checkpoint, unbriefed count and source health.

## LLM stage 1: triage (M4)

- **Provider/models:** Groq via plain `httpx` (OpenAI-compatible API), not the vendor SDK: an LLM call
  is an HTTP POST, so it reuses `request_with_retry` and the fake-transport tests. Models:
  `openai/gpt-oss-20b` (stage 1), `openai/gpt-oss-120b` (stage 2, M5). The Llama models on Groq do
  NOT support strict JSON-schema output; the gpt-oss models do (checked against Groq docs 2026-09-20).
- **Strict structured output** (`response_format: json_schema, strict: true`) = constrained decoding.
  We still validate each entry with Pydantic (duplicate index, blank summary, ...) and drop bad entries.
- **Failure policy:** retry happens at the level of the *run*: items the model skips or botches stay
  `discovered` and are retried next time. Each batch is persisted as soon as it succeeds. After
  2 consecutive failed batches we stop (circuit breaker) instead of hammering an API that is down or
  rate-limited. Known gap: a "poison" batch that always fails would be retried forever (add an
  attempts counter if it ever happens).
- **Prompt-injection stance:** scraped titles/text are untrusted. They are delimited as data, the
  system prompt says never to obey them, and the schema only permits a category, a score and a
  sentence, so the worst a hostile item can do is mis-score itself. The LLM has no tools or actions.
- **Every enrichment records `model` + `prompt_version`** so results can be recomputed later.

Measured on 158 real items (2026-09-20):
- Free tier is rate limited (many 429s, `Retry-After` 11-18s); the retry layer absorbed it, 2m24s total.
- Scores are NOT calibrated: 21% got importance 4, none got 5, and arXiv agent papers scored high with
  zero popularity signal. Cross-source items did score higher (3 sources: 4.0 vs 2.7 for one source).
- **Design consequence for M5:** select by rank (top N by importance, then deterministic signal
  strength), never by an absolute threshold.
