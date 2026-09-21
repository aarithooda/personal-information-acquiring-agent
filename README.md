# PIA: Personal Intelligence Agent (V1)

A personal research scout that answers one question: **"What important things have happened since I last checked?"**

It watches a small set of sources (research papers, AI labs, Hacker News, GitHub, math and physics writing),
remembers when you last looked, and hands you a short briefing of only what is new *to you*. The briefing
scales with how long you were away: after a day you get about 1 headline; after ten days, about 15.

PIA is also a **learning project**: the code is small and readable on purpose, and
[docs/design.md](docs/design.md) records why each significant decision was made, what the alternatives were,
and what would change later.

The layout (values here are illustrative):

```
Since you last checked
Last checked 2026-09-20 17:30 UTC (3 days ago). Skimmed 84 new items.

## 🔥 Worth knowing
### [Title of a significant development](https://…)
Two to four sentences on what happened, written only from the source text.
**Why it matters:** one sentence for you.
*via arxiv, hf_papers, hn*

## 🤖 AI & Agents      - [Item](https://…): one-line summary
## 💻 Software         - [Item](https://…)
## 🔬 Research         - [Item](https://…): one-line summary
```

## Setup (Windows)

You need Python 3.11+ and a free [Groq](https://console.groq.com) API key.

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Put your key in a file named `.env` in the project folder:

```
GROQ_API_KEY=your_key_here
TYPESAFE_API_KEY=your_jev_key_here   # optional: enables Jev at Stage 1 (JEV_API_KEY also works)
```

To use Jev you also need your interest profile: copy `config/interests.example.toml` to `config/interests.toml` and
write your own. It is git-ignored because it describes you personally.

`.env` (and `.env.txt`, in case Notepad added the extension) is git-ignored. PIA never prints the key.

Then check the setup and run it:

```bash
.venv\Scripts\pia.exe doctor --online
```

## Running it

**Double-click `run-pia.bat`.** It runs `pia` and keeps the window open so you can read the briefing.
(Double-clicking `pia.exe` directly closes the window the moment it finishes.)

From a terminal, `run-pia.bat <arguments>` or `.venv\Scripts\pia.exe <arguments>` both work. The first run takes a
few minutes because Groq's free tier rate-limits the initial batch of items; later runs are much faster.

| Command | What it does | Changes data? |
|---|---|---|
| `pia` | Fetch what's new, triage it, write and save your briefing, advance the checkpoint | **Yes** |
| `pia show` | Re-read the latest briefing that had content (`pia show 3` for a specific one, `--raw` for plain markdown) | No |
| `pia history` | List every past check, newest first | No |
| `pia status` | Checkpoint, items waiting, items given up on, health of each source | No |
| `pia doctor` | Check Python, API key, config and database (`--online` also tests each source and your key) | No |
| `pia collect` | Fetch and store items only; no briefing, checkpoint unchanged | Stores items |
| `pia enrich` | Run the LLM triage on stored items and print what it decided | Stores triage |
| `pia web` | Start the local web UI: New, Library, Favorites (see below) | Only your favorites (and upgrades an old schema) |

Options go before the command: `pia --db other.db status`, `pia -v` (show warnings and retries),
`pia --profile other.toml`, and `pia --triage jev|llm|auto` (which Stage 1 to use; the default `auto` uses Jev when a
key and a profile exist, otherwise the LLM, and prints which one it chose; `jev` fails loudly if either is missing).
The four read-only commands open the database in SQLite's read-only mode, so they can never alter it, create it,
or upgrade an old schema.

Every check is recorded, including "nothing new" ones, so the newest entry in `pia history` is often an empty
check. `pia show` skips those by default.

## Web UI: New, Library, Favorites

A small local web page over the same database. **Double-click `run-pia-web.bat`** (or run `pia web`) and your browser
opens at `http://127.0.0.1:8765`.

| Tab | What it shows |
|---|---|
| **New** | The latest briefing that had content, laid out as headline cards and one-line lists. A picker lets you open any past briefing |
| **Library** | Everything you have been shown, with search and category/source filters. Tick "Include items the briefing skipped" to reach the rest of the history |
| **Favorites** | Whatever you starred with the ☆ button, newest first. Stars are saved in the same SQLite file (`data/pia.db`), so they survive restarts |

The web UI installs from an optional extra, once: `.venv\Scripts\python.exe -m pip install -e ".[web]"` (the `dev` extra
already includes it).

- **It does not fetch anything.** There is no "check now" button: run `run-pia.bat` (or `pia`), then refresh the page.
- **It is local-only.** It listens on `127.0.0.1`, checks the `Host` header, sends no CORS headers and a strict
  Content-Security-Policy, and `pia web` refuses non-local addresses. It shows your reading history.
- **Reading is read-only.** Only the star buttons write, and only to the `favorites` table. Interactive API docs are at
  `http://127.0.0.1:8765/docs`.
- **Web text is shown as text, never as HTML**, so a hostile title cannot run code in the page.

## How it works

```
sources  ->  normalize + dedupe  ->  SQLite  ->  what is new to you?  ->  LLM triage
(arXiv, HF papers,                   (items)      (state, not dates)      (per item)
 HN, GitHub, RSS)                                                              |
                                              ranking  ->  LLM editor  ->  briefing  ->  commit + checkpoint
                                              (code)       (shortlist)     (code)        (one transaction)
```

Code decides everything that must be exact; the LLM only supplies judgment.

- **Deterministic code:** fetching and retrying, URL identity and dedup, timestamps, what counts as "new", how many
  items to show, ranking, layout, and the checkpoint.
- **Stage 1, Jev (default when configured):** a decision model. For each item it answers nine small typed questions
  (topic match, substance, low-value pattern, wildcard, ...) using *your interest profile*, and code combines the
  answers into a relevance score. About 0.4 s per item, a fraction of a cent for a full run, one request per item run
  four at a time. Jev cannot write text, so it produces no summaries.
- **Stage 1, LLM (`--triage llm`, and the automatic fallback if Jev is down):** `gpt-oss-20b` gives each item a
  category, an importance score and a one-line summary as schema-constrained JSON, validated again in code.
- **Stage 2 (`gpt-oss-120b`):** the editor. It sees a shortlist side by side, picks the developments that truly matter
  (at most the budget, fewer if little happened) and explains them using only the text provided. With Jev it also reads
  your profile, so "why it matters" is tied to your interests.

**"New" means new to you.** Items are chosen by state (not yet part of a briefing), never by comparing dates, so
something published last week that PIA only discovered today still counts. The checkpoint is simply the last saved
briefing; it moves in the same transaction that saves the briefing.

**How much you see** is `floor(1.5 x days away)` headlines (minimum 1, days capped at 14), plus two one-line extras
per headline. It is a ceiling, not a quota. Change the rate in `src/pia/briefing/budget.py`.

## Configuration

Sources live in `config/sources.toml`. Adding a blog or feed needs no code:

```toml
[[source]]
type = "rss"
name = "my_blog"
url = "https://example.com/feed.xml"
```

Other source types (`arxiv`, `hf_papers`, `hn`, `github`) take their pre-filter settings there too, such as arXiv
categories, minimum Hacker News points, or minimum GitHub stars.

| Knob | File |
|---|---|
| Headlines per day away, extras per headline | `src/pia/briefing/budget.py` |
| First-run lookback (3 days), maximum lookback (14 days), overlap | `src/pia/collect.py` |
| Models, prompts, prompt version | `src/pia/llm/prompts.py` |
| Triage batch size, attempts before giving up on an item | `src/pia/llm/enrich.py` |
| Jev questions, the weights that combine the answers (version 0, unvalidated), concurrency | `src/pia/jev/triage.py` |
| What Jev and the editor are told about you | `config/interests.toml` (compiled by `src/pia/profile.py`) |

## Privacy and safety

- **Sent to Jev (TypeSafe):** for every item, a *trimmed* copy of your profile (interest areas, signals and rules; not your
  name, character descriptions, current projects or learning style), the item's title, source type and up to 500
  characters of its text. TypeSafe states it does not train on customer data. Popularity numbers are not sent.
- **Sent to Groq (editor, Jev mode):** the shortlisted items plus your profile as text (everything except your name).
- **Sent to Groq (LLM triage mode):** item titles, the first few hundred characters of each item's text, source names and popularity
  numbers. Not your identity, not your reading history. The API key travels only in the `Authorization` header to
  `api.groq.com`.
- **Stays local:** the database (`data/pia.db`) and every briefing. Both are git-ignored.
- **Web text is untrusted.** Titles and snippets are passed to the models as delimited data, the prompts forbid
  following instructions found in them, and the output schema only allows a category, a score and text. The models
  have no tools, so the worst a hostile item can do is mis-score itself.
- **The web UI is local-only** (127.0.0.1) and never sends your data anywhere.
- **No arbitrary page fetching.** PIA only calls the source APIs and feeds listed in `config/sources.toml`.

## Reliability

PIA treats sources and the LLM as unreliable. A source that fails keeps its own cursor and catches up later; if every
source fails, or triage fails completely, nothing is saved and the checkpoint does not move; a bad LLM batch is split
to isolate the culprit; a crash at any moment loses nothing. `tests/test_resilience.py` proves this by randomly
breaking sources and the LLM across many runs and checking database invariants after every one.

## Reading the code (a suggested order)

1. `models.py`, `normalize.py`, `db.py`: the data shape, item identity, the schema and its migrations
2. `state.py`, `collect.py`, `pipeline.py`: checkpoints, cursors, and the fixed sequence of steps
3. `sources/base.py` and one adapter such as `sources/hackernews.py`: parse (pure) vs fetch (network)
4. `http.py`: the single place that retries
5. `llm/client.py`, `llm/prompts.py`, `llm/enrich.py`: structured output, untrusted input, batching, failure
6. `briefing/rank.py`, `briefing/budget.py`, `llm/headlines.py`, `briefing/curate.py`: from scores to a briefing
7. `profile.py`, `jev/client.py`, `jev/triage.py`: one profile compiled for several consumers, typed questions in and validated answers out, and combining answers in code
8. `history.py`, `doctor.py`, `cli.py`: read-only access and the command line
9. `web/`: `favorites.py` and `queries.py` (data), `briefing_view.py` (parsing saved briefings), `app.py` (the API), `static/` (the page)
10. `tests/`: `fakes.py` (a scriptable LLM), `test_pipeline.py` (your scenarios), `test_resilience.py` (fault injection)

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
| Decompose a judgment into atomic questions, combine in code | `jev/triage.py` (`build_questions`, `derive`) |
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

## Testing

```bash
.venv\Scripts\python.exe -m pytest
```

No test reaches beyond your own machine (any outside connection fails the test) and none touches your real database.
HTTP is replaced by a fake transport, the LLM by a scriptable fake, and time by an injected clock. The front-end logic
is tested with Node when it is installed.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Window closes immediately | Use `run-pia.bat`, or run `pia` from a terminal |
| Anything unclear | `pia doctor --online` |
| "GROQ_API_KEY not found" | The key must be in `.env` (or `.env.txt`) in the project folder |
| First run is slow | Groq's free tier rate-limits; PIA waits and retries automatically |
| "Nothing was recorded" | Triage or every source failed, so the checkpoint was deliberately not moved. Run `pia` again |
| `pia web` says it needs extra packages | `.venv\Scripts\python.exe -m pip install -e ".[web]"` |
| Web page is empty | Run `run-pia.bat` first to create a briefing, then refresh |
| `--triage jev` says the key or profile is missing | Add `TYPESAFE_API_KEY` to `.env` and create `config/interests.toml`; `pia doctor --online` checks both |
| Emoji show as boxes | Use Windows Terminal; the old console font lacks them |

## Known limitations and what is next

- **Most items are judged on a title alone**, mainly Hacker News (measured: 58.6% of the first real briefing's 157
  items). Article-text extraction is a deliberate future consideration, **not implemented**; the analysis, options and
  constraints are in [docs/design.md](docs/design.md) under "Future consideration: article text".
- **Jev's scoring is version 0 and unvalidated.** The weights in `jev/triage.py` are reasoned, not tuned. Measured on
  the first real run: title-only items (Hacker News) score systematically low because there is little to judge,
  and the shortlist is top-K by score, so a run can be all AI papers and no mathematics even though the profile asks
  for discovery. See docs/design.md, "Jev at Stage 1", for the data and the options.
- arXiv is fetched newest-first and capped, so it is a sample, not a complete feed.
- The LLM editor tends to fill every headline slot; watch for padding.
- The web UI cannot start a check itself (deliberately; see docs/design.md, D8) and reads old briefings by parsing their saved text.
- Manual runs only: there is no scheduler. Times are shown in UTC.

Planned direction (not built): V2 learns your interests; V3 semantic search over what you have seen; V4 research
deep dives across sources; V5 more agentic workflows; V6 personal research memory.
