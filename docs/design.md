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

## Briefing: adaptive size, ranking, editor (M5)

- **Size scales with time away** (user requirement): "Worth knowing" slots = `max(1, floor(1.5 x days))`
  -> 1 day: 1, 3 days (first run): 4, 10 days: 15. One-line extras: 2 per headline slot. Days are capped at
  the 14-day fetch lookback. `HEADLINES_PER_DAY` / `ALSO_PER_HEADLINE` in `briefing/budget.py` are scale
  factors, not per-item cutoffs.
- **The budget is a ceiling.** The editor (stage 2) sees a shortlist of `2 x budget + 2` and may pick fewer.
- **Ranking is relative, never thresholded:** `importance + popularity percentile (per source, within
  this window) + cross-source boost`. `other` and importance-1 ("noise") items are never shown.
- **Two LLM stages, different jobs:** stage 1 scores each item alone (cheap, generous); stage 2 (gpt-oss-120b)
  compares the shortlist side by side (listwise) and writes 2-4 sentence explanations from the provided
  text only ("do not invent facts").
- **Graceful degradation:** editor fails -> ship the deterministic top-N with summaries and say so.
  Triage fails completely -> `EnrichmentFailed`, nothing committed, checkpoint unmoved. Triage partly
  fails -> brief on what exists; the rest stays pending for the next run.
- **Item settlement:** shown = `briefed`; considered but left out = `skipped` (both attached to the
  briefing, never "new" again); not-yet-triaged items stay untouched.
- `run_briefing` takes an injected `prepare` step (plain list or LLM curator), so orchestration stays
  generic and testable.

Observed live (2026-09-20, same 158 items): headlines 1 / 4 / 15 for 1 / 3 / 10 days. Limits found:
- Explanations are only as good as the source text. Papers have abstracts; HN links have only a title, so
  they are hard to summarize. See "Future consideration: article text" below.
- In all three runs the editor filled every slot. The "choose fewer" path is covered by unit tests but
  not yet observed with the real model; watch for padding and tune the editor prompt if it persists.

## Reliability pass (M6)

- **No invented summaries.** Items with no text (whitespace counts as none) get an empty summary
  regardless of what the model said; the prompt also tells it to return "". The briefing shows such
  extras as a bare link. Measured live: 0/32 title-only items kept a summary. PROMPT_VERSION -> `stage1-v2`.
- **Error taxonomy:** `LLMUnavailable` (no answer: outage, 429, bad key; never the item's fault) vs
  `LLMBadOutput` (unusable answer: cut off, not JSON, no usable entries; may be the item's fault).
- **Poison items:** on `LLMBadOutput` a batch is split in half recursively, isolating a culprit in
  ~log2(batch) extra calls instead of starving every batch behind it. Unavailable errors never count
  against items. Items get `triage_attempts` (schema v2); at 3 failed attempts they become `failed`
  (excluded from briefings, counted in `pia status`).
- **Schema migration v2** (`ALTER TABLE ... ADD COLUMN`) verified on a copy of the real v1 database.
- **Prompt changes do not strand work:** `enriched_items` uses each item's latest enrichment whatever
  its `prompt_version`.
- **Bug found by fault injection:** a source with no successful fetch yet looked back from *now*, so a
  source that was down at first launch permanently lost what it had published meanwhile. First-run
  lookback is now anchored to the source's first *attempt* (still capped by MAX_LOOKBACK).
- **Fault-injection tests** (`tests/test_resilience.py`): random source outages, LLM outages, garbage
  output and process crashes across 25 seeds (plus 400 more run once, all passed), and a sweep that kills
  the process at every LLM call. After every run: status and briefing links agree, no triage result is
  lost or orphaned, briefing count and checkpoint match what was committed; after a healthy run nothing is
  stuck and every source item is in the database.

Considered and not done: proactive rate-limit pacing from `x-ratelimit-*` headers (reactive retry
worked; revisit if daily runs feel slow).

Finding: **most items are title-only** (58.6% of the first real briefing's 157 items; 80% of the newest 40), mostly Hacker News. Triage and the editor judge most items on
the title alone. Analysis and options: see "Future consideration: article text" below. Not implemented.

## Future consideration: article text (V1.5 / V4). NOT IMPLEMENTED

**Status:** documented only, by decision of the project owner (2026-09-21). V1 does not fetch arbitrary
web pages. This section records the finding, the options and the constraints, so the decision can be
made later with the reasoning intact.

### The finding

Two measurements, both real data:

- 2026-09-20, the **newest 40** items triaged with `stage1-v2`: 32 (**80%**) had no text. Newest-first
  over-samples Hacker News, so this is an upper bound.
- 2026-09-21, **all 157** items in the first real briefing (query below): **58.6%** title-only.

Either way, a clear majority of items are judged on a headline.

| Has text | Source of that text |
|---|---|
| arXiv, HF Daily Papers | the paper abstract |
| GitHub | repo description + topics |
| Quanta, DeepMind, OpenAI (RSS) | the feed summary |
| **Title only** | Hacker News (Algolia returns title + URL; `story_text` exists only for Ask/Show posts), RSS feeds without summaries (e.g. HF blog) |

Hacker News is also the largest source by volume, which is why title-only items dominate.

Consequences observed or implied:
1. Triage and the editor rest on weak evidence for most items. Both prompts already forbid guessing.
2. Since M6, title-only items get no summary at all, so they appear as bare links in the extras.
3. **Evidence-availability bias:** papers ship with abstracts and HN stories do not, so the briefing leans
   toward papers. That reflects what data we hold, not what matters to the reader.
4. Editor explanations for HN-origin developments are necessarily thin.

Both samples are a single day. Re-measure over a week of real data before acting. The share of title-only
items among triaged items is one query (verified against the real database):

```sql
SELECT ROUND(100.0 * SUM(TRIM(COALESCE(i.content_raw, '')) = '') / COUNT(*), 1) AS pct_title_only,
       COUNT(*) AS triaged
FROM items i JOIN enrichments e ON e.item_id = i.id;
```

### Options

| | Approach | Gain | Cost / risk |
|---|---|---|---|
| A | Use text HN already provides (Ask/Show `story_text`) | Small | Almost none; no new fetching |
| B | Fetch only page metadata (`og:description`, `<meta name=description>`) | Medium | Still fetches arbitrary URLs |
| C | Full main-content extraction (readability-style) | Highest | Highest: security, legal, tokens |
| D | Do B/C **lazily, for the editor shortlist only** (about 2N+2 items, not all ~150) | High | Bounds cost, load and exposure |
| E | V4 deep dive: extraction becomes a tool an agent chooses to call | Highest | Needs the tool-safety design below |

Likely path when the time comes: **A immediately if trivial; D as V1.5** behind a fetch-policy layer;
**E (V4)** reuses that same layer as an agent tool instead of building a second fetcher.

### Constraints any implementation must respect

- **Placement:** after ranking, before the editor. The long tail stays title-triaged; the shortlist gets
  real text. Decide then whether stage 1 gets a second pass or the editor alone benefits.
- **Raw vs derived data (D4 again):** never overwrite `items.content_raw`. Store fetched text in its own
  table (e.g. `item_texts`: item_id, final_url, fetched_at, http_status, content_type, text,
  extractor_version, error), so it is cacheable, re-runnable and records failures. Schema migration v3.
- **Provenance in prompts:** the editor should know which text came from the source and which was fetched.
- **Graceful degradation:** a fetch failure leaves the item title-only. It must never block a briefing or
  touch the checkpoint. Negative-cache failures so a dead URL is not retried every run. Extend the
  fault-injection suite: fetch chaos must not change any invariant that holds today.
- **Bounded work:** per-request timeout, response size cap, per-run time budget, per-domain rate limit,
  limited concurrency, `text/html` (and plain text) only.
- **Security (the main reason this is deferred):**
  - URLs come from untrusted feeds, so this is a **server-side request forgery (SSRF)** surface. Allow only
    http/https; refuse loopback, private, link-local and cloud-metadata addresses; re-validate after every
    redirect (and cap redirects); send no cookies or credentials.
  - Fetched pages are a much larger **prompt-injection** surface than titles. The current defence (delimit
    as data, forbid obeying it, schema-constrained output, no tools) is adequate while the LLM cannot act.
    In V4, once an agent has tools, this needs tool allow-lists and no secrets in the model's context.
  - Parsing untrusted HTML is itself an attack surface: pin and review the extractor dependency
    (candidates to evaluate: `trafilatura`, `readability-lxml`).
- **Legal and etiquette:** respect robots.txt and paywalls, use a distinct User-Agent, keep extracted text
  as a local cache for personal use with limited retention, never redistribute it.
- **Token budget:** stage-2 input grows with real text, and Groq's free tier is already token-rate-limited
  (see M4). Truncate deliberately and keep the shortlist small.
- **Testing:** recorded HTML fixtures, `httpx.MockTransport`, and dedicated SSRF cases (decimal/octal IPs,
  redirects to 127.0.0.1, IPv6 forms, DNS names that resolve to private addresses).

### How to decide

1. Re-measure the title-only share over at least a week of real data.
2. Read a few real briefings: are the HN-origin headlines and extras noticeably thinner than paper items?
3. Cheap experiment before building anything: hand-check ~20 HN stories and compare the editor's picks from
   titles alone against picks with the article text pasted in. If the picks barely change, defer further.
4. Only then choose A, D or E.

### Concepts to take from this

- **Evidence-availability bias:** a model (or ranker) favours whatever has the most evidence, which is not
  the same as what matters. Watch for it whenever input quality varies by source.
- **Trust boundaries:** every URL fetched from the open web crosses one. SSRF and prompt injection are the
  two classic failures.
- **Raw vs derived data**, and **graceful degradation**: both are already V1 rules; this feature must obey them.

