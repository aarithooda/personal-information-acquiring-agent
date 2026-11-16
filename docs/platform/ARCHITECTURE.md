# PIA platform architecture

How the platform is built and why. Decisions are numbered so later changes can refer to them. The baseline they
start from is in [AUDIT.md](AUDIT.md); what they must deliver is in [PRD.md](PRD.md).

## Shape

One Python process, one SQLite file, one static web app.

```
                 +------------------------------ pia serve ------------------------------+
 sources         |  worker thread (scheduler)                    FastAPI (HTTP API)      |
 RSS / Atom  --> |   acquire -> classify -> assess -> rank        /api/...   <---------- | <-- web app (browser)
 arXiv, HN,      |      |          |          |        |          /api/device/... <----- | <-- Windows companion
 GitHub, HF,     |      v          v          v        v                                 |
 JSON APIs,      |   +-------------------------------------------------------------+     |
 page watch,     |   |  SQLite (WAL): items, sources, topics, states, briefs,      |     |
 Gmail (r/o)     |   |  opportunities, relations, notifications, usage, FTS5 index |     |
                 |   +-------------------------------------------------------------+     |
                 |      relate -> notify -> discover -> expire                           |
                 +-----------------------------------------------------------------------+
                                  |                       |
                              Groq (LLM)       Jev (integrated, dormant)
```

The same code runs on a laptop (`pia serve`, loopback, no login) and on a small cloud host (a container with a
volume, password login, the companion pointed at its URL).

## Decisions

| # | Decision | Alternatives considered | Why |
|---|---|---|---|
| A1 | **Evolve, do not restart.** Keep the proven lower layers (HTTP retry, canonical URLs, source parsers, Groq and Jev clients) and build a new store, pipeline, API and UI on them. | Rewrite from scratch; bolt features onto the briefing schema | The lower layers are small, tested and correct. The briefing schema encodes the wrong lifecycle (an item is consumed by a briefing), so it cannot simply grow. |
| A2 | **The v1 briefing pipeline is frozen, not deleted.** It keeps its own database and tests. The old web UI is removed because the new one replaces it. | Delete v1; keep both UIs | The published evaluation describes exactly that code; keeping it runnable keeps the evidence checkable. It costs nothing at runtime. Retire it once a new benchmark supersedes the old one. |
| A3 | **SQLite (WAL) with FTS5, plain SQL, forward-only migrations.** | PostgreSQL; an ORM | One user, one writer process, tens of thousands of rows a year. SQLite gives transactions, full-text search and a single file to back up, with zero operations. The SQL is ordinary, so a move to Postgres later is mechanical. Revisit if a second writer process or vector search is needed. |
| A4 | **A new database file** (`data/platform.db`). v1 data is imported read-only by `pia import-legacy`. | Migrate `pia.db` in place | The old file is never modified, so nothing of value can be lost and v1 keeps working. |
| A5 | **One process: API plus an in-process scheduler thread.** `pia run-once` exists for hosts that prefer cron. | Celery/Redis; separate worker service | The workload is a few hundred HTTP fetches and a few dozen model calls an hour. A queue would be more moving parts than work. Each thread uses its own connection; WAL and a busy timeout handle the rest. |
| A6 | **FastAPI JSON API + a no-build web app (Preact and htm as vendored ES modules).** | React/Vite/TypeScript build; server-rendered templates with HTMX | The app needs real client state (optimistic state changes, drawers, filters), which templates make awkward. A build pipeline would add a Node toolchain to a Python project for a single maintainer. Vendored ES modules give components with nothing to compile and nothing fetched from a CDN at runtime. |
| A7 | **Deterministic first, models second.** Classification, dedup, topic matching, popularity and relation finding are code. Models are called in three places only (assessment, briefs and extraction, source evaluation). | Send every item through an LLM | Cost, latency, rate limits, and the benchmark's finding that a model-free ordering was competitive. The product must keep working with no keys. |
| A8 | **Raw model output is stored separately from derived scores.** `judgements` keeps what each model said; item score columns are recomputable. | Store only final scores | Same rule v1 established (D4, J2): formulas change, model calls should not have to be repeated. |
| A9 | **Importance and relevance are different numbers.** Importance is "how significant is this in its field"; relevance is "how much does this owner care". Rank blends them; notifications read both. | One score | The owner's own profile states the distinction. It also makes personalisation inspectable: a model judges significance, the interest profile judges fit. |
| A10 | **Interest profile lives in the database**: topics with an explicit level and a learned affinity. The legacy TOML seeds it and still supplies taste lists (high-value signals, low-value patterns) to prompts. | Keep TOML as the source of truth | The UI must edit it and behaviour must update it. |
| A11 | **Sources have a lifecycle** (`candidate`, `active`, `paused`, `rejected`) and an origin (`builtin`, `user`, `discovered`). Discovery only ever creates candidates. | Auto-follow discovered sources | The brief requires oversight; it also contains the damage of a bad discovery. |
| A12 | **Server decides notifications, clients deliver.** A `notifications` row is the fact; the web app and the Windows companion each track their own delivery. | Client-side rules | Rules stay in one place and work when the laptop is off. |
| A13 | **Auth: none on loopback, password + session cookie otherwise, bearer tokens for devices.** Refuse to bind a public address with no password set. | Always require login; OAuth provider | Local use stays frictionless; a cloud deployment is never accidentally open. Stdlib scrypt, no auth dependency. |
| A14 | **Personal details stay out of the repository.** The database, env files, `config/interests.toml` and `config/personal.toml` are git-ignored. | Commit the profile | The repository is public. |
| A15 | **Groq only: Jev is integrated but dormant** (owner's decision, 2026-11-16). `[providers] jev = false` in `config/models.toml`. While off, PIA builds no Jev client, does not read the Jev key, runs no fit step, and ranking ignores Jev judgements stored earlier (they stay in the database). The v1 briefing also defaults to Groq (`--triage llm`). The code (`pia.jev`, `intel/jevfit.py`) and its tests are kept. | Remove Jev; leave it enabled whenever a key exists | One external provider is simpler to reason about, to cost and to audit, and the evaluation gives no evidence that Jev improves ranking. Keeping the code costs nothing and keeps the option open. A key in the environment must never be enough to switch a provider on. |

## Data model

SQLite, UTC ISO-8601 timestamps, `now` passed in rather than read from the clock (as in v1).

| Table | Purpose |
|---|---|
| `domains` | The information spaces. Slug, name, position, personal `weight`, learned `affinity`. |
| `topics` | Flat topic and entity set. `kind` (topic, org, person, tech), `domain_id`, explicit `level` (-2 muted .. 3 priority, NULL = unrated), learned `affinity`, `origin`, `aliases`. |
| `sources` | Registry and lifecycle. `kind`, `config` JSON, `status`, `origin`, `purpose` (news or opportunities), `trust`, schedule (`interval_minutes`, `next_fetch_at`), health (`last_ok_at`, `last_error`, `failures`), `evaluation` JSON for candidates, `org_id`. |
| `fetch_runs` | One row per fetch attempt; the per-source cursor is the last successful `fetched_until`. |
| `items` | One row per canonical URL. Facts (title, source text, dates, signals), `kind`, primary domain, derived scores (`importance`, `relevance`, `quality`, `context_boost`, `score`), state timestamps (`read_at`, `saved_at`, `dismissed_at`), processing stage. |
| `item_sources` | Every source that reported an item (multi-source sightings merge here). |
| `item_domains`, `item_topics` | Associations with a strength. |
| `judgements` | Raw model output per item, task, provider, model and prompt version. |
| `briefs` | The written explanation: summary, why it matters, key points, kind-specific sections. |
| `item_texts` | Fetched full text (reader cache) with provenance and failures. |
| `relations` | Typed, scored links between items. |
| `opportunities` | Extracted fields and the owner's tracking status for opportunity items. |
| `organisations` | The owner's watch list; watched pages are `sources` rows pointing at an organisation. |
| `feedback`, `events` | Explicit feedback and the behaviour log that personalisation learns from. |
| `notifications`, `devices`, `deliveries` | Notification facts, registered companions, per-device delivery. |
| `llm_usage` | One row per model call: provider, model, task, tokens, estimated cost, latency, outcome. |
| `runs` | Background job history (acquire, process, discover) for the status page. |
| `settings`, `sessions` | Key-value settings; login sessions. |
| `items_fts` | FTS5 index over title, text and summary. |

## Pipeline

Each stage is idempotent and resumable; an item records the stage it has reached.

1. **Acquire.** For every `active` source that is due: compute the window from its cursor (with overlap), fetch
   through the shared retry layer, parse, upsert by canonical URL. A failure is recorded against the source, backs
   it off exponentially, and never affects other sources. Repeated failure marks the source unhealthy in the UI
   but never deletes it.
2. **Classify (deterministic).** Domains from the source's declared domains, arXiv categories and a per-domain
   keyword lexicon; item kind from source kind and patterns; topics by matching topic names and aliases.
3. **Prescore (deterministic).** Popularity percentile within the source, cross-source sightings, source trust and
   interest match give a provisional importance and relevance, so every item is rankable before any model runs.
4. **Assess (model, batched, budgeted).** `gpt-oss-20b`, strict JSON schema, about ten items per call: confirms
   domain and kind, scores significance, extracts topics and entities, writes a one or two sentence summary.
   Highest prescore first, capped per run; anything not reached keeps its deterministic scores.
5. **Personal fit (dormant).** Off by default. Only if `[providers] jev = true` in `config/models.toml`, a key
   and `config/interests.toml` are all present do shortlisted items get a typed personal-fit judgement from Jev,
   blended into relevance and stored raw. While it is off this step does not exist: no call, no key read, and
   judgements stored earlier are ignored by ranking.
6. **Rank.** `score = 0.45 importance + 0.40 relevance + 0.15 popularity`, then multiplied by a slow recency decay
   and raised by `context_boost`. Muted topics and low source quality subtract. All weights are named constants in
   one module.
7. **Relate.** Candidates share a topic or entity, or match in the FTS index; scored by title-token overlap and
   shared entities; explicit links (an item whose text cites another item's URL) become `references`. A new item
   raises the `context_boost` of the older items it relates to.
8. **Extract opportunities.** Items classified as opportunities go through a structured extraction
   (`gpt-oss-120b`), with deterministic date parsing as a check and a fallback.
9. **Notify.** Unnotified items above the owner's thresholds become notifications, subject to caps, quiet hours
   and one-per-story.
10. **Brief (lazy).** The full structured brief is written for featured items and on first open of any item.

## Model routing

| Task | Default | Why this model | Without keys |
|---|---|---|---|
| Classification, dedup, topic match, relations | Code | Exact and free | Same |
| Assessment (significance, topics, short summary) | Groq `openai/gpt-oss-20b`, batched | The benchmark could not separate it from Jev on ranking, and it also returns text. About a tenth of Jev v2's cost per item when batched. | Deterministic prescore only |
| Personal fit | The interest profile, in code | Explicit levels and learned affinity are inspectable and free. **Jev is integrated but dormant** (decision A15): it would add typed probabilities against the owner's written taste, but no superiority over the LLM triage is established. | Same |
| Full brief | Groq `openai/gpt-oss-120b` | Longer, structured writing from source text | One-line summary or source text |
| Opportunity extraction, source evaluation | Groq `openai/gpt-oss-120b` | Low volume, accuracy matters (deadlines, eligibility) | Regex dates; heuristics-only evaluation |

Every row above that calls a model calls Groq. Providers sit behind two small protocols (`LLM.complete_json`, the Jev client), and every call goes through one
usage recorder, so swapping a provider or model is a configuration change plus a price entry. Prices are
configuration (`config/models.toml`), not constants in code, because they change; the status page shows spend per
day, per task and per model.

## Security

- **Untrusted text.** Everything fetched is data. Prompts delimit it and forbid following it; outputs are
  schema-constrained; models have no tools. The UI renders text nodes only.
- **Fetching arbitrary URLs** (reader, feed discovery, page watch) goes through one guarded fetcher: http(s) only,
  resolved addresses must be public (no loopback, private, link-local or metadata ranges), redirects re-validated
  and capped, response size and time capped, no cookies, robots.txt honoured for page fetches.
- **Auth.** Loopback binding needs no login and validates the `Host` header (DNS-rebinding defence). Any other
  binding requires a password (scrypt), issues an HttpOnly SameSite=Strict session cookie, and checks `Origin` on
  state-changing requests. Devices use hashed bearer tokens that can be revoked.
- **Secrets** come from the environment or a git-ignored env file and are never logged or returned by the API.
- **Gmail** is read-only by scope (`gmail.readonly`), so the integration cannot send, modify or delete mail even
  if the code were wrong.

## Deployment

Cloud-first target: one container (`Dockerfile`) with a persistent volume for `data/`, HTTPS in front, the
environment providing the keys and `PIA_PASSWORD`. Any host that runs a container with a disk works; the
operations guide compares options. Backups: `pia backup` writes a consistent snapshot (SQLite online backup API)
and prunes old ones; the worker takes one daily.

Deploying is the owner's decision (it has a recurring cost) and is not done by the build.
