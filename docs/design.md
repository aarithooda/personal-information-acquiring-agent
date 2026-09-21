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


## Polish (M7): inspection, diagnostics, packaging

- **Read-only is a separate connection type, not a convention.** `history.connect_readonly` opens SQLite
  with `mode=ro` and `PRAGMA query_only`, refuses to create a missing file, and never migrates. `status`,
  `history`, `show` and `doctor` use it. Tests prove the database file is byte-for-byte unchanged, and
  writing through it raises. This also fixed a real defect: the old `status` used `connect()`, which
  silently upgraded an old-schema database on disk.
- **`pia show` defaults to the latest briefing that had content.** Every check is recorded, including
  "nothing new" ones, so the newest row is often empty (this confused the first real run).
- **`pia doctor` never prints the key** (found / where / length / shape only), makes no writes, and treats
  the network as opt-in (`--online`): it tries each source over the last day, storing nothing, and asks
  Groq for its model list (no data sent). Exit code 1 only for failures, not warnings. A schema older than
  the code is a warning ("will upgrade"), a newer one is a failure.
- **`run-pia.bat`:** double-clicking `pia.exe` closes the window on exit, so the launcher pauses only when
  given no arguments (scriptable otherwise) and passes through pia's exit code. CRLF is pinned in
  `.gitattributes`. The no-argument path is not executed by tests because it runs the real pipeline.
- **Docs are tested:** every CLI command must appear in the README, and the README's "add a source" TOML
  example must load. A `tests/conftest.py` fixture makes any real socket or DNS use in a test fail, which
  turns "no test touches the network" from a promise into a guarantee.

Verified on the real database: history/status/show/doctor left `data/pia.db` byte-identical, and so did
a full test run. WAL mode keeps a `-shm` read-index file next to the database; it is a cache, not data.

Status: **V1 complete** (M1-M7). Deliberately deferred: article-text extraction (see above), a scheduler,
interest learning (V2).

## Web UI: New, Library, Favorites (W1-W5)

A minimal local web UI built *over* the core. Approved decisions (2026-09-21):

**Core-change budget.** `db.py`: one appended migration (v3, `favorites`). `cli.py`: one added command
(`pia web`). `pyproject.toml`: an optional `web` extra. Everything else lives in `src/pia/web/`. Verified at
the end with `git diff --stat` against the pre-UI commit.

| # | Decision | Alternatives | Why / trade-off | Concept |
|---|---|---|---|---|
| D1 | FastAPI + uvicorn, JSON API, plain `def` handlers | Flask, Django, stdlib `http.server` | Pydantic already in use; typed response models; free OpenAPI docs at `/docs`. Two new deps. | API as a contract; blocking I/O runs in a thread pool |
| D2 | Vanilla JS + static HTML, no framework, no build | React/Vue, HTMX + templates, Streamlit | Three screens; shows the browser platform and the API directly. More manual DOM code. | Separation of front end and back end; XSS: untrusted text only via `textContent` |
| D3 | "New" = latest briefing that had content (same default as `pia show`); structure parsed from the stored markdown | Store headline explanations in a new table (changes the core, cannot recover old briefings) | Works for every historical briefing with no core change. Couples the UI to the renderer's format, guarded by a contract test that parses real `render_briefing` output; unknown format falls back to raw text. | Read model; contract test |
| D4 | `favorites(item_id PK, created_at)` + idempotent `PUT`/`DELETE /api/items/{id}/favorite` | `POST /toggle` | A toggle gives the wrong state on double-send or two tabs; PUT/DELETE converge. | Idempotency |
| D5 | Read endpoints use `connect_readonly`; only the favorite endpoints write. Migrations run when the app is created. One connection per request, opened inside the handler. | One shared connection | Reuses the M7 guarantee. SQLite connections are bound to the thread that created them and handlers run in a thread pool. WAL lets `pia` run while the UI is open. | Thread affinity; least privilege |
| D6 | Local only: bind 127.0.0.1, validate the `Host` header, no CORS, CSP header, `pia web` refuses non-loopback hosts | Bind 0.0.0.0 | Personal reading data; a webpage you visit must not be able to drive a local app (CSRF, DNS rebinding). | Threat model for localhost |
| D7 | Library = everything shown in a briefing; toggle to include items the briefing skipped (off by default); favorites are always visible whatever their status | Shown only | Keeps all historical data reachable without cluttering the default view. | |
| D8 | No "Check now" button in this version | Run the pipeline from the UI | Multi-minute job needs background execution and progress; it is the only action that spends network and API quota, which raises the localhost-security stakes. Run `run-pia.bat`, then refresh. | Long-running work vs request/response |

Plan: W1 migration + favorites data layer; W2 read API + briefing parser; W3 favorites API + security tests;
W4 front end; W5 `pia web`, launcher, docs, browser verification.

### Web UI: what was built and what checking it in a real browser found (W4-W5)

Built as designed: `pia web` (127.0.0.1:8765, `--open`, refuses non-local hosts, friendly message if the
`web` extra is missing), `run-pia-web.bat`, and a three-tab single page (New, Library, Favorites) over the API in
`web/app.py`. The front end is `index.html` + `app.css` + `logic.js` (pure, Node-tested) + `app.js` (DOM).

Verified in a real browser against a *copy* of the real database: briefing picker over all past briefings; headline
cards with explanation and "why it matters"; star click -> `PUT` -> UI, tab counter, API count and the SQLite row all
agree; favorites survive a full page reload; Library shows 16 items by default and 186 with the skipped toggle;
"Load more", search and category filters combine; un-starring in Favorites removes the card and the row; no console errors.
A deliberately hostile item (`<img onerror>`, `<script>`, a `javascript:` URL, an HTML summary) was displayed as inert
text: nothing injected, no script ran, and the URL was not made clickable.

**Bug found only by using it:** Library and Favorites shared one filter object, so a search made in Library silently
applied to Favorites, which then said "No favorites yet" while hiding a real favorite. Fixes: filters are per tab; empty
states distinguish "nothing exists" from "filters are hiding things" and offer "Clear filters".
Lesson: an empty state is a claim about the data, and it must be true.

**Structural change that followed:** the pure decisions (query building, empty-state wording, the http(s)-only link
rule) moved to `logic.js` and are unit-tested with Node (`tests/test_web_logic.py`), because DOM code cannot be
tested without a browser. Static rules (no `innerHTML`, no inline code, valid syntax, explicit PUT/DELETE) are tests too.

**Test-suite change:** the "nothing leaves this machine" guard now allows loopback only, because asyncio's event loop
(used by FastAPI's TestClient) opens an in-process loopback socket pair on Windows. The guard has its own test.

**Process notes:** the favorite endpoints were written in the same file as the read endpoints before their tests; a
mutation check (four deliberate breakages, all caught) was used to show the tests are real. Known limits: the UI
depends on the renderer's layout (contract-tested, raw-text fallback), Library ordering within a briefing is by
importance because headline rank is not stored, and there is no "Check now" (D8).

## Jev at Stage 1 (decision model + interest profile)

Change requested 2026-09-21: keep the architecture (collect -> triage -> rank -> shortlist -> editor -> commit) and
swap what runs inside Stage 1. Jev (a "System One" decision model) triages every new item cheaply against the reader's
TOML interest profile; the stronger Groq model (`gpt-oss-120b`) stays the editor and now reads the same profile.

**Core-change budget.** New: `profile.py`, `jev/client.py`, `jev/triage.py`. Small edits: `db.py` (migration v4, three
nullable columns on `enrichments`), `state.py` (`save_enrichments`/`enriched_items` carry the new columns), `rank.py`
(uses continuous `relevance` when present), `http.py` (`SourceError.status`), `config.py` (Jev key lookup),
`llm/prompts.py` + `llm/headlines.py` (profile-aware editor prompt, identical without a profile), `briefing/curate.py`
(optional `triage` and `profile` parameters), `cli.py` and `doctor.py` (options and checks). With neither a Jev key nor
a profile, behaviour is exactly what it was (`--triage llm` and `auto` without a key).

| # | Decision | Alternatives | Why / trade-off | Concept |
|---|---|---|---|---|
| J1 | Nine atomic typed questions per item (2 Score, 6 Noul, 1 Choice), combined in code | One "worth showing?" Noul | Jev's docs say one proposition per question; a compound subjective question is exactly what a literal decision model is worst at. More requests-worth of tokens, still under a cent per run. | Decompose judgments, combine in code |
| J2 | Store the RAW answers (`details` JSON) + `profile_hash`; `derive()` works from the stored form | Store only the final score | The weights are version 0 and unvalidated; with raw answers they can change with zero new API calls (tested). | Raw vs derived data |
| J3 | Plain httpx client over our retry layer, not the vendor SDK | `typesafe-sdk` | One JSON endpoint; SDK adds a dependency (and its own HTTP stack) without capability. Typed models validate every response. | An LLM/model call is an HTTP POST |
| J4 | One request per item, 4 in flight, backpressure, results saved as they arrive, all SQLite writes on the main thread | Submit everything to the pool | Found by a test: submitting all requests up front let an outage burn the whole backlog before the circuit breaker could react. Bounded in-flight work fixes it. | Backpressure |
| J5 | Errors reuse the LLM taxonomy: 400/422 = `LLMBadOutput` (may be this item), everything else = `LLMUnavailable`; `SourceError.status` makes the difference visible | Treat all as one error | Same attempts-counting and circuit-breaker logic as the LLM triage; an outage is never held against an item. | Error taxonomy |
| J6 | Fallback: if Jev stops responding, the LLM triage finishes the remaining items | Fail the run | Graceful degradation as everywhere else in PIA. Mixed rows rank together (importance vs 1+4 x relevance). | Graceful degradation |
| J7 | Profile: TOML source of truth; `jev_state()` (trimmed) for Jev, `llm_text()` for the editor; content hash stored with results; git-ignored, example committed | Markdown/JSON/YAML | Human-editable with comments, stdlib parser; Jev warns of "context rot", so personal fields and prose are not sent to it. Measured: the trimmed profile is still ~4k tokens, 40x a typical item. | One source, several renderings |
| J8 | Popularity signals are NOT sent to Jev | Send them | The ranker already handles popularity (per-source percentiles); keeping it out avoids rewarding "popular but unrelated". | Separation of concerns |
| J9 | `--triage auto\|jev\|llm`; `jev` is strict, `auto` degrades with a printed reason; an invalid profile is always an error | Silent fallback | Silently doing something else after being asked for Jev would mislead; ignoring a file the reader wrote would hide their mistake. | Fail loudly on explicit intent |

**What the real API taught us (a spike before any tests were written).** Response format matched the docs exactly;
`GET /v1/models` exists (used by `doctor --online`, sends no item data); latency about 430 ms per request (1.3 s cold);
about 4.4k input tokens per request, dominated by the profile. And: the first wording of the injection question
("tries to give instructions to whoever processes it") scored 0.74 on the plain imperative title "Exfiltrate Your
Weights". It was reworded to target text aimed at a model and is only acted on at 0.90+; measured 0.37 on that title
and 0.99 on a synthetic "ignore all previous instructions" item (which also scored ~0 on every other question).

**First real run** (copy of the real database, 186 items, 5 days away => 7 headlines, shortlist 16, real Jev + real
Groq editor): 34 s end to end including collection, no warnings or retries. Jev: 820,795 input tokens = **$0.0345**
for all 186 items ($0.00019/item). Relevance median 0.32, max 0.81; injection flags 0 (no false positives);
category agreement with the old Stage 1 77%, rank correlation 0.71. The formula's ceiling is about 0.85, so
importance 5 is never produced (ranking uses the continuous relevance, so this only affects the integer column).

**Findings and open questions (not yet acted on):**
1. **Title-only items are penalised.** Mean relevance 0.23 for the 113 title-only items vs 0.58 for the 73 with text;
   Hacker News averages 0.22, arXiv 0.68. Little text means little "substance" evidence, so an evidence-availability
   bias enters the score. Concrete misses by the profile's own standard: "One-Electron Universe" (0.37) and "I vibed a
   proof of Conway's conjecture" (0.35). `too_little_info` separates the groups only weakly (0.79 vs 0.64).
   Options: score title-only items on topical match alone and renormalise; use `too_little_info` to shrink toward a
   neutral prior; or fetch article text (see "Future consideration: article text").
2. **Top-K shortlists are homogeneous.** 20 of 21 shown items are AI; the Quanta mathematics story (0.73) was skipped.
   The profile's `discovery` section asks to avoid an echo chamber, but the editor only sees the shortlist. Options: a
   reserved "discovery lane" (top items by wildcard x substance outside the top K), or per-category quotas / MMR.
3. **Hype repos.** Items about Jev itself average 0.49 vs 0.35 for the rest (9 of 16 at 0.5+; three GitHub repos at
   0.74-0.77) despite the profile saying not to surface items merely for containing AI terms. Whether that is
   description-level keyword matching or a self-reference effect cannot be separated without labelled data.
4. **The weights are unvalidated.** The benchmark from the Jev investigation (human labels, an A1 arm = the current LLM
   with the same profile, recall@K, calibration) is still the way to tune them and to test whether the profile, not the
   model, is doing most of the work.
5. **Profile size.** ~4k tokens per request against ~100-token items; an ablation with a shorter profile is cheap.
6. Extras are now bare links (Jev writes no summaries); the editor only writes explanations for headlines.

## Benchmark: labelled evaluation of Stage 1 (tooling only; `benchmarks/`)

Requested 2026-09-21, before any change to the ranking formula. Goal: measure the current Jev Stage 1 and the current LLM
Stage 1 against **human labels** on the frozen 186-item dataset, so later changes (a discovery lane, a title-only fix, new
weights) are judged on evidence. **No production code changed**: the benchmark lives in `benchmarks/` and imports `pia`; the
only edits outside it are `.gitignore` (personal data), and `pyproject.toml` (`pythonpath` gains `.` so tests can import it).
Full explanation, metric glossary and limits: [benchmarks/README.md](../benchmarks/README.md).

| # | Decision | Alternatives | Why / trade-off | Concept |
|---|---|---|---|---|
| B1 | Ground truth = the reader's own SHOW / MAYBE / SKIP labels; existing "shown" flags and every model score are never used as truth | Use briefing decisions or favourites | Shown items were chosen by the system under test (circular), and unshown items never had a chance to be wanted (selection bias) | Independent ground truth |
| B2 | Freeze the items in `items.json` (facts only, content-hashed); the labelling module imports nothing that can read model output (tested) | Label from the live database | A fixed set makes numbers comparable over time; blindness by construction, not by promise | Blinding |
| B3 | Seeded random queue; ~40 items repeated far apart; append-only labels, latest wins | Database order; overwrite a file | Random order spreads fatigue and makes any labelled prefix a random sample (so partial analysis is fair, if noisy); repeats measure the labeller's own consistency, the ceiling for any ranker | Test-retest reliability |
| B4 | An **arm** is a file of RAW Stage 1 output; scoring re-runs PIA's own `enriched_items` + `rank_items`; `rescore` re-derives Jev relevance from stored answers with no API calls | Re-implement the ranking; store only final scores | Measures what PIA really does and lets a formula change be re-evaluated for free (J2 again). The curator's inline filter is pinned by a contract test that runs the real `make_curator` | Raw vs derived; contract tests |
| B5 | Metrics at K = 4/10/16/32 (the editor's shortlist sizes for 1/3/5/10 days away) plus AP, AUC, nDCG, Spearman; strict (SHOW) and lenient (SHOW+MAYBE); `capture@K` = hits / min(K, #SHOW) | One headline number | Recall@K bounds what the briefing can contain (Stage 2 cannot resurrect an item Stage 1 dropped); capture removes the cap that a small SHOW set puts on recall and precision | Recall vs precision; ceilings |
| B6 | 95% bootstrap intervals over items; arms compared on the SAME resamples (paired) | Point estimates | With ~25-40 SHOW items a proportion has a +-0.15-0.20 interval; a bare number would overstate what 186 items show | Uncertainty; paired comparison |
| B7 | Each arm reported twice: production ranking, and model score alone | Production only | Separates what the model contributes from what popularity and cross-source boost add | Ablation |
| B8 | Partial analyses are labelled PRELIMINARY and withhold every item-level list; `arm` and `snapshot` print counts only | Show everything | Naming items next to model scores mid-labelling would contaminate the labels still to be made | Blinding |
| B9 | Every labelled item gets a `fold` (0-4, from its URL hash); tuning must be cross-validated or use fresh labels | Tune and test on all 186 | Fitting ~8 weights to 186 labels and reporting on the same 186 flatters the result | Overfitting |
| B10 | Decision rules are fixed and committed before any label exists: label mapping SHOW 2 / MAYBE 1 / SKIP 0 (nDCG gains; AP counts SHOW only); guardrail `new title_only recall@16 >= current Jev title_only recall@16`; the benchmark is a development/evaluation benchmark, not evidence of generalization; no change to methodology, labels or rules after seeing results, and a substantive change is a new `BENCHMARK_VERSION` printed on every report | Adjust the rules as results arrive | Choosing metrics or thresholds after seeing the numbers lets the data pick the story (the "garden of forking paths"); a version stamp makes any change visible instead of silent | Pre-registration; researcher degrees of freedom |

Not measured: Stage 2 (the editor's picks and prose), calibration of `relevance`, any other reader or period, and the "profile
matters more than the model" hypothesis (needs a third arm: LLM + the same profile). Status when written: tooling complete and
tested; labelling not yet done (waiting on the reader), so **no result is claimed**.

Observed while freezing the baselines (real APIs): Jev returned HTTP 503 for every request on 2026-09-21, so the run stopped
after the circuit breaker tripped and, by design, **saved nothing** (0 of 186 scored) instead of a misleading partial arm;
finished items are kept in a scratch database and a re-run resumes without paying twice. The LLM arm met Groq's usual 429
rate limits (absorbed by the retry layer) and at least one HTTP 400 on a batch (that batch's items stay pending and are retried).

## Jev layer v2 (question set `jev-triage-v2`)

Requested 2026-09-21: make the Jev integration as strong as it can be, **from TypeSafe's documentation and first
principles, not by tuning against any labelled data**. Benchmark v1 stays frozen and was not run. Sources read:
docs.typesafe.ai `primitives`, `primitives/{noul,score,choice}`, `confidence`, `patterns/{composite-scoring,confidence-routing,fan-out}`,
`concepts/state`, `models`, `api`, `introduction/machine-learning-primer`, `cookbooks/{rerank_typesafe,consistency_noul_cookbook}`,
and **`model-jaggedness/jev-1.13`** (known limitations).

### What the documentation says, against what v1 did

| # | Documented | v1 | Verdict |
|---|---|---|---|
| D1 | One condition per Noul; a compound question's value "means less" | `low_value_pattern`, `wildcard`, `buildable` each asked about a whole LIST (a 13-way disjunction) | defect |
| D2 | Score levels: concrete, independent, ONE dimension | `interest_match` mixed topic and priority tier in compound levels; `substance` mixed technical-ness and significance | defect |
| D3 | A Score's decimal has "weak numerical calibration"; probabilities are the calibrated part | `derive` used `score / 3` as a precise scale and ignored `probabilities` | defect |
| D4 | "Accuracy falls as the state grows with content unrelated to the decision"; send only necessary fields | every question saw the whole ~4k-token profile; questions needing no profile (category, injection) saw it too | defect |
| D5 | "Literal reading": implied conditions are read at face value | `buildable`: "could **plausibly** become a component..." | defect |
| D6 | Confidence "tells you whether to act", not what the answer is | stored, never used; `too_little_info` computed and never used | under-use |
| D7 | Aliases move; pin versions if results are compared over time | default `jev-latest`, no way to pin | gap |
| D8 | Questions are independent and cheap to fan out | 9 questions | under-use |

### Decisions

| # | Decision | Alternatives | Why / trade-off | Concept |
|---|---|---|---|---|
| K1 | One Noul per ENTRY of each profile list (each low-value pattern, exception, valued signal, wildcard, building interest), pointing at the entry with the documented backtick path; combined with `max` | One Noul per list | D1. The reader's own words define the taste (nothing is invented); each answer is inspectable ("penalty_by: low.pattern.7"). `max` (not a sum) because the probabilities are correlated and the docs guarantee no structural invariants, so thirteen weak 0.2s must not add up to a penalty. Cost: about 69 questions per item. | Atomic questions; fuzzy OR |
| K2 | Priority is ONE Choice over the profile's own tiers plus `none`; the expected rank weight of its probability distribution is used | A Score rubric; the Choice winner only | D2, D3. Docs: use Choice for "which of these" and offer an "other/none". Weights are `(n - rank) / n`: the profile orders tiers but gives no magnitudes, so linear-by-rank invents none. | Expected value over a distribution |
| K3 | No Score question feeds the formula | Keep Scores | D3 | Use the calibrated part of an answer |
| K4 | Four requests per item, each with only the profile sections its questions point at; the item-only request (category, injection, subject unclear) sends NO profile | One request with the whole profile | D4. Independence of questions is documented, so splitting loses nothing. Cost: ~6.5k input tokens per item (v1 4.4k; about $0.0003 per item), ~1.6 s per item, 4x the requests (well under the documented 1,200/min at 4 workers). | Context rot; least data |
| K5 | `relevance = reach x (1 - low_value) x (0.5 + 0.5 x value)`; `reach = max(interest, build, discovery)` | v1's additive blend; the docs' plain weighted sum; noisy-OR; a plain product | Mirrors the profile's own rule ("substantive AND (on-topic OR buildable OR surprising), minus low-value"). Any one route to relevance suffices; substance alone earns nothing (v1 paid 35% credit for it); `value` can at most HALVE relevance, so evidence that a title cannot carry never erases a strong fit. The `0.5` is the only constant, a named PRIOR (`VALUE_FLOOR`), not fitted. The docs' weighted sum is a valid pattern; it was not chosen because it lets substance compensate for zero interest. | Non-compensatory AND/OR; bounded modulation |
| K6 | `subject_unclear`, the models' confidences and whether the item had text are stored beside the score and never inside it. Title-only is a FACT the code knows, so it is recorded (`facts.title_only`) and never asked | Fold `too_little_info` in as a penalty or shrink toward a prior | D6. A property test pins that `relevance` is identical for the same answers whether or not the item had text. **This does not "solve" title-only items**: the questions are worded to be answerable from a title, and the bounded `value` factor limits the effect of missing evidence, but a title-only item can still score lower because its signal answers are lower. A structural fix (a reserved lane for uncertain-but-promising items, per the docs' confidence-gated routing) is deferred. | Evidence vs relevance; missing data |
| K7 | Popularity and cross-source signals stay OUT of the Jev decision layer (as in J8) | Fold them into relevance | They are a separate axis (public salience vs personal fit). **Flagged, not changed:** `rank.py` adds up to +2 on a 1-5 scale (about half the range) and gives items with no popularity metric (arXiv, RSS) 0; that weighting is unexamined and shared with the LLM triage. | Separate concerns |
| K8 | `LEGACY_V1` stays the default of the low-level `JevTriager`; the production factory `make_jev_triage` uses the current design. `design_for(question_set)` maps a stored row to the code that derives it. v1 code and stored v1 rows are untouched | Replace v1 | The frozen benchmark tooling constructs `JevTriager` directly and must keep measuring what it measured. | Versioned formats |
| K9 | Stored row: raw answers for every question, `plan` (what the ids mean), `facts`, `evidence` (which question drove reach, value and penalty), per-request usage, all model ids seen. `JEV_MODEL` pins the version | Store only the score | Raw preserved (D4 of the project's principles): the formula can change and old rows can be re-derived offline. | Raw vs derived |
| K10 | Questions are built from what the profile HAS; a profile with no tiers, building interests or wildcards is refused with a clear message; the doctor reports per-request tokens | Assume every section exists (v1 asked about `building_interests` even when absent) | Robustness for anyone else's profile | Fail loudly |

### Observed (a plumbing check, not evidence of quality)

Against the real API, on 8 hand-written synthetic items (none from the benchmark), with the checks declared beforehand and no
constant or wording changed from what came back: all four requests per item were accepted; all 69 questions answered
(3 + 19 + 38 + 9); about 1.6 s per item; 6.5k input tokens per item (the doctor's estimate: 6.7k). Directions were sane: an
on-topic title-only tool scored 0.73, off-topic and marketing items about 0.02, an injection attempt was detected (0.98) and
zeroed. The docs state no limit on questions per request; 38 in one request was accepted.

### Deliberately NOT changed

`rank.py` (popularity/cross-source arithmetic); the curator's hard gates (`category == "other"` and `importance <= 1` drop an item
outright, an absolute cutoff at relevance 0.125); a discovery lane or uncertainty routing to the editor; batching several items
in one request (not documented as supported); self-consistency sampling (the docs report a per-question standard deviation of
about 0.01, so it would buy little); parallelising an item's four requests (a latency optimisation).

### Risks and open questions

Unknown per-request question limits (none documented; 38 worked); the calibration of per-entry Nouls is unmeasured, and the
docs say a Noul and its negation need not sum to 1, so absolute levels may drift between question types; `max` ignores
corroboration between several matching entries; the wildcard route is weighted like any other, so the profile's "unusually
high novelty" threshold is enforced only through the bounded `value` factor; an item costs four round trips, so one failing
request fails the item (no partial credit, no per-group retry); and **the design is untested against human judgment and may
not be better than v1 or than the LLM triage**.

### How this must be evaluated

Not on benchmark v1's labels. They have been seen, and so has an analysis of them, by whoever designed v2, which contaminates
them as an evaluation of a redesign even though no coefficient was fitted to them. Use fresh labels (a v2 benchmark, per
its rule 7). The frozen tooling needs small additions before it can run v2: `run_jev_arm` needs a `design` argument (today it
uses the legacy design on purpose); `arms._export` records `QUESTION_SET_VERSION` (v1) as the arm's question set; the
analysis' wildcard group reads `answers["wildcard"]` (v2 has `wildcard.N`); and `rescore_jev_arm` should dispatch through
`pia.jev.triage.design_for`. Those are tooling changes and belong to the new benchmark version, not to this one.

## Benchmark v2: a fresh corpus for `jev-triage-v2` (`benchmark_v2/`)

Requested 2026-09-21. Benchmark v1 stays frozen and untouched; v2 evaluates the production `jev-triage-v2` on a corpus v1 never saw, with
the methodology fixed in [benchmark_v2/METHODOLOGY.md](../benchmark_v2/METHODOLOGY.md) before any label or model output exists.

| # | Decision | Alternatives | Why / trade-off | Concept |
|---|---|---|---|---|
| E1 | 160 new items + 40 repeats of v1 items; the 160 are the primary evaluation set, the repeats only measure label consistency and are excluded from the primary metrics | Score all 200 together | Repeats are not independent test items; they answer "how stable are the labels?" (v1-label vs v2-label) | Independence; test-retest |
| E2 | New items are a **seeded simple random sample** of everything PIA's sources would have returned over a past 60 days, selected by canonical-URL identity only | Hand-pick, stratify, or pick "interesting" items | Any selection by content re-introduces the selector's taste; a uniform sample keeps the natural source, topic and title-only mix | Sampling bias |
| E3 | The period ends 2026-09-17, before v1's intake began, and every new item is checked absent from v1 by canonical URL and by (source, external id) (and against the real database) | Rely on the date range or titles | No temporal or identity overlap with v1 | Leakage |
| E4 | arXiv, HN and RSS replay through the **production adapters unchanged**; GitHub and HF Daily Papers (which cannot fetch a past window) use thin historical variants reusing the production parser, query, filters and caps | Modify the production adapters; use a different collector | Same machinery and `RawItem` representation without touching production. Declared deviation: only the date parameters differ | Same mechanism |
| E5 | Popularity signals are as of collection time (hindsight) and are never shown to the reader; declared as a limitation | Pretend they are historical | The APIs cannot give point-in-time values | Honest limits |
| E6 | Labels reuse v1's blind labelling code unchanged; one screen per item; repeats are ordinary corpus items with neutral ids and a shuffled order | New UI; within-session repeats | The v1 screen shows exactly what Jev is sent and nothing else; a 200-screen queue with no origin information cannot reveal which are repeats | Blinding |
| E7 | Primary metrics are graded/lenient (nDCG@16, lenient AP, lenient AUC) with bootstrap intervals and a permutation test; strict SHOW metrics are descriptive; decision rules D1 to D4 are fixed in advance | Strict recall@K as primary | v1 showed strict SHOW labels are too rare for inference at this size; this is a statistical lesson about prevalence, independent of any v2 item | Power |
| E8 | `FREEZE.json` binds the corpus id, manifest hash, methodology hash and the production code's git tree hash, config hashes and profile hash; `verify` recomputes them; model output existing before the labels are frozen is flagged | A promise | "We changed nothing after seeing results" becomes checkable | Pre-registration |
| E9 | Model-free reference orderings (newest, text-first, popularity) are pre-declared as comparators alongside the LLM Stage 1 | Compare only against LLM Stage 1 | A trivial heuristic was competitive on a different corpus; a result must be judged against it | Baselines |

Not measured: Stage 2, calibration, other readers or periods. The author of the methodology had seen Benchmark v1's results; METHODOLOGY.md section 2
states exactly what that did and did not influence.
