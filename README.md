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
```

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

Options go before the command: `pia --db other.db status`, `pia -v` (show warnings and retries).
The four read-only commands open the database in SQLite's read-only mode, so they can never alter it, create it,
or upgrade an old schema.

Every check is recorded, including "nothing new" ones, so the newest entry in `pia history` is often an empty
check. `pia show` skips those by default.

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
- **LLM stage 1 (`gpt-oss-20b`):** for each item, a category, an importance score and a one-line summary, returned as
  schema-constrained JSON and validated again in code.
- **LLM stage 2 (`gpt-oss-120b`):** the editor. It sees a shortlist side by side, picks the developments that truly
  matter (at most the budget, fewer if little happened) and explains them using only the text provided.

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

## Privacy and safety

- **Sent to Groq:** item titles, the first few hundred characters of each item's text, source names and popularity
  numbers. Not your identity, not your reading history. The API key travels only in the `Authorization` header to
  `api.groq.com`.
- **Stays local:** the database (`data/pia.db`) and every briefing. Both are git-ignored.
- **Web text is untrusted.** Titles and snippets are passed to the models as delimited data, the prompts forbid
  following instructions found in them, and the output schema only allows a category, a score and text. The models
  have no tools, so the worst a hostile item can do is mis-score itself.
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
7. `history.py`, `doctor.py`, `cli.py`: read-only access and the command line
8. `tests/`: `fakes.py` (a scriptable LLM), `test_pipeline.py` (your scenarios), `test_resilience.py` (fault injection)

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

## Testing

```bash
.venv\Scripts\python.exe -m pytest
```

No test touches the network or your real database. HTTP is replaced by a fake transport, the LLM by a scriptable
fake, and time by an injected clock.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Window closes immediately | Use `run-pia.bat`, or run `pia` from a terminal |
| Anything unclear | `pia doctor --online` |
| "GROQ_API_KEY not found" | The key must be in `.env` (or `.env.txt`) in the project folder |
| First run is slow | Groq's free tier rate-limits; PIA waits and retries automatically |
| "Nothing was recorded" | Triage or every source failed, so the checkpoint was deliberately not moved. Run `pia` again |
| Emoji show as boxes | Use Windows Terminal; the old console font lacks them |

## Known limitations and what is next

- **Most items are judged on a title alone**, mainly Hacker News (measured: 58.6% of the first real briefing's 157
  items). Article-text extraction is a deliberate future consideration, **not implemented**; the analysis, options and
  constraints are in [docs/design.md](docs/design.md) under "Future consideration: article text".
- arXiv is fetched newest-first and capped, so it is a sample, not a complete feed.
- The LLM editor tends to fill every headline slot; watch for padding.
- Manual runs only: there is no scheduler. Times are shown in UTC.

Planned direction (not built): V2 learns your interests; V3 semantic search over what you have seen; V4 research
deep dives across sources; V5 more agentic workflows; V6 personal research memory.
