# Personal Information Acquiring Agent (PIA)

PIA is a personal information intelligence platform. It runs continuously, acquires developments across the
fields you care about, judges what matters, learns your interests, and brings the result to you: a web app with
a Home page, a space per field, an opportunities tracker, and notifications on your desktop.

It is not a news aggregator, an RSS reader or a chatbot. You do not ask it questions; it works in the background
and you read what it found.

> **Status.** A working first release that runs on your own computer: `pia serve`. Cloud hosting is designed and
> packaged (a container) but **not deployed**; Gmail and the Windows companion are implemented and tested but
> **need a one-time setup by you**. [docs/platform/ROADMAP.md](docs/platform/ROADMAP.md) says exactly what is
> implemented, tested, partial or waiting on a decision.

## What it does

- **Home**: the 5 to 12 items most worth your attention now, across every space, plus what is closing soon.
- **Spaces**: one per field (AI, software, mathematics, physics, robotics, quantitative finance, economics,
  geopolitics, startups, science and technology, research opportunities, hackathons and competitions, your
  university, security, space). Each has a complete feed with a **Latest** and an **Important** view.
- **Every item**: unread, read, saved or dismissed, on every device. Opening one shows a brief written for the
  kind of thing it is (a paper is explained differently from a model release or a geopolitical development),
  why you are seeing it, connected earlier and later items, and the original link.
- **Opportunities**: internships, programmes, fellowships, hackathons and competitions with deadlines and
  eligibility up front, a tracker (Interested, Planning to apply, Applied, Accepted, Rejected), and monitoring of
  the organisations you choose. Expired opportunities are never shown as open.
- **Interests**: an inspectable profile. Rate topics a space at a time, mark the few that matter most, mute what
  you do not want. PIA also learns from what you save, like and dismiss, and shows you what it learned.
- **Sources**: what is monitored, how healthy each source is, and suggestions PIA found and measured. Nothing
  discovered is followed until you say so.
- **Notifications**: three levels (Interesting, Important, Critical) with thresholds, daily caps and quiet hours,
  delivered to Windows by a small companion script.
- **Search** across everything acquired.

## Quickstart

Requires Python 3.11 or newer.

```bash
git clone https://github.com/aarithooda/personal-information-acquiring-agent.git
cd personal-information-acquiring-agent
python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
pip install -e ".[dev]"
```

Optional but recommended, for summaries and briefs: copy `.env.example` to `.env` and set `GROQ_API_KEY` (a free
key from console.groq.com is enough). Without any key PIA still acquires, classifies, ranks and links items; it
just cannot write summaries.

Optional: copy `config/personal.example.toml` to `config/personal.toml` and edit it (your university's name, your
stage, starting interest levels). It is git-ignored.

```bash
pia serve --open
```

That starts the web app at http://127.0.0.1:8765 and the background agent. On Windows you can double-click
`run-pia.bat` instead. The first cycle fetches about 75 sources and takes a few minutes; items appear as it goes.

If you used PIA v1, bring its data across (the old file is only read, never changed):

```bash
pia import-legacy
```

## Commands

| Command | What it does |
|---|---|
| `pia serve` | The web app plus the background agent. `--open` opens the browser, `--no-agent` serves only. |
| `pia web` | The same as `pia serve` (kept so old shortcuts work). |
| `pia run-once` | One cycle of the agent, then exit. For cron-style hosts. `--no-fetch`, `--no-models`, `--force`. |
| `pia seed` | Adds new spaces, topics and catalogue sources from `config/`. Never overwrites your edits. |
| `pia import-legacy` | Imports v1's items, triage results and favourites. |
| `pia backup` | Writes a consistent snapshot to `data/backups/` (safe while running; also taken daily). |
| `pia set-password` | Protects PIA when it listens on anything other than this computer. |
| `pia add-device` | Creates a token for the Windows companion. |
| `pia gmail-connect`, `pia gmail-disconnect` | Authorise or remove **read-only** Gmail access. See [docs/platform/GMAIL.md](docs/platform/GMAIL.md). |

The platform keeps its data in `data/platform.db` (`--data` or `PIA_DATA` to change it).

## Configuration

| File | Purpose | Committed? |
|---|---|---|
| `config/taxonomy.toml` | The spaces, their vocabulary and seed topics | yes |
| `config/catalog.toml` | The vetted source catalogue (142 sources, each one fetched and checked) | yes |
| `config/models.toml` | Which model does which job, budgets, and prices for the cost meter | yes |
| `config/personal.toml` | Your starting profile | **no** (git-ignored) |
| `config/interests.toml` | Your written taste; read only by v1 and by Jev when it is switched on | **no** (git-ignored) |
| `.env` | API keys. Only `GROQ_API_KEY` is used | **no** (git-ignored) |

A catalogue entry looks like this. Day to day you add sources in the app (Sources, Add a source) instead:

```toml
[[source]]
name = "Example Lab Blog"
kind = "rss"
url = "https://lab.example/feed.xml"
homepage = "https://lab.example"
description = "Research posts from an example laboratory."
domains = ["ai"]
tier = "core"        # "core" starts active; "optional" starts as a suggestion
trust = 0.75
interval = 240       # minutes between fetches
```

## How it works

```
sources -> acquire -> classify -> assess -> rank -> relate -> notify
            (code)     (code)    (model)   (code)   (code)    (code)
```

Deterministic code does everything it can: deduplication by canonical URL, sorting items into spaces, matching
topics, popularity percentiles, finding related items, and ranking. Models are called in three places only:
a batched assessment of new items (`openai/gpt-oss-20b` on Groq), full briefs and opportunity extraction
(`openai/gpt-oss-120b`), and evaluating suggested sources. Every call is metered; the System page shows the cost,
and a daily budget stops model calls when reached.

**Groq is the only external model provider PIA uses.** Jev (TypeSafe's decision model) is still integrated, as
an optional second opinion on personal fit, but it is **dormant**: with `jev = false` under `[providers]` in
`config/models.toml` (the default) PIA makes no Jev call, does not read the Jev key, and ignores any Jev
judgement stored earlier when ranking. The project's own blind evaluation
([docs/evaluation.md](docs/evaluation.md)) showed Jev ranks above chance and could **not** show it is better
than the LLM triage, so no claim of superiority is made and nothing depends on it.

Design documents: [product requirements](docs/platform/PRD.md), [architecture and decisions](docs/platform/ARCHITECTURE.md),
[audit of v1](docs/platform/AUDIT.md), [sources](docs/platform/SOURCES.md), [operations](docs/platform/OPERATIONS.md).
Status: [what remains and who it waits on](docs/platform/ROADMAP.md), and
[every original requirement with its status](docs/platform/COMPLETION.md).

## Security and privacy

- **Your data stays local.** The database, backups, keys, `config/personal.toml` and `config/interests.toml` are
  git-ignored.
- **What leaves your machine.**
  - **Sent to Groq:** item titles and source text for assessment and briefs; for briefs, a short description of
    you (your stage, the spaces and topics you rate highly). Not your name, not your university, not your reading
    history.
  - **Sent to Jev (TypeSafe):** nothing. Jev is switched off. If you ever switch it on (`[providers] jev = true`,
    plus a key and `config/interests.toml`), it would receive the item (source type, title, up to 500 characters
    of text) and the slices of that profile its questions point at, not your name.
  - Check each vendor's current data-handling terms before sending anything you consider sensitive.
- **Fetching pages** goes through one guarded fetcher: public addresses only, redirects re-checked, size and time
  capped, robots.txt honoured. Full text is shown in full only for open-access papers; otherwise an excerpt.
- **The web app** listens on this computer only unless a password is set; it refuses to start on a public
  address without one. It serves no inline script and loads nothing from other sites.
- **Gmail** is read-only by scope and is off until you authorise it.

## Testing

```bash
pytest
```

868 tests, no network (a guard makes any real connection fail), no API keys. GitHub Actions runs them on every
pull request to `main`, on Ubuntu and Windows. They cover acquisition and
failure handling, deduplication, migrations, ranking, personalisation, state transitions, opportunity extraction
and deadlines, notifications, the API, authentication, the guarded fetcher, and the browser-side logic (with Node).

## Limitations

- Not deployed to the cloud yet, so acquisition stops when this computer is off.
- No source for university announcements yet: the institute's website could not be reached for verification. Mail
  integration or a page address you supply will fill this.
- The opportunities space is thin until you list organisations to monitor; broad opportunity listings are
  deliberately off.
- Ranking weights are stated priors, not fitted values. No labelled evaluation of the platform's ranking exists
  yet, and the v1 benchmark corpus is spent.
- Sources that render entirely with JavaScript cannot be watched as pages.
- arXiv is sampled (the newest N per category group), not complete.

## The v1 briefing pipeline

PIA began as a terminal briefing tool ("what happened since I last checked?"). That pipeline is frozen, still
runnable and still tested, because the published evaluation describes exactly that code. Its commands use their
own database (`data/pia.db`):

| Command | What it does |
|---|---|
| `pia` | Fetch, triage, rank and print a briefing (needs `GROQ_API_KEY`). |
| `pia collect`, `pia enrich` | The two halves of that, separately. |
| `pia status`, `pia history`, `pia show` | Read-only inspection of past briefings. |
| `pia doctor` | Checks setup; never prints a key. `--online` also tests the network. |

v1 also uses Groq by default (`--triage llm`). Its Stage 1 can use Jev only when asked for: set
`TYPESAFE_API_KEY`, copy `config/interests.example.toml` to `config/interests.toml`, and pass `--triage jev`
(or `--triage auto`). Details: [docs/jev.md](docs/jev.md),
[docs/design.md](docs/design.md) (the v1 decision log) and [docs/reading-the-code.md](docs/reading-the-code.md).

## License

MIT. See [LICENSE](LICENSE).
