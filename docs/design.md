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
