# Roadmap, status and handoff

The handover document: what is done, how well it is verified, what remains and who it is waiting on.
Updated 2026-11-16.

The status of every requirement in the original brief, one by one, is in [COMPLETION.md](COMPLETION.md). This
file works at the level of capabilities and of the work still to do.

Status words mean exactly this:

- **Tested**: implemented, covered by automated tests, and exercised against the real thing where one exists.
- **Implemented**: built and covered by automated tests, but not yet exercised against the real external service.
- **Partial**: works, with a stated gap.
- **Blocked**: cannot proceed without the owner.

## Status

| Capability | Status | Evidence, and what is missing |
|---|---|---|
| Store: schema, migrations, FTS5, backups | Tested | `tests/test_platform_store.py`; a v1-to-v2 schema upgrade was run on a real database |
| v1 data import | Tested | 500 real items, triage results imported; old file byte-identical afterwards |
| Acquisition: feeds, arXiv, HN, GitHub, HF | Tested | Live run: 1,325 items from 76 of 77 sources in 133 s; failure isolation, backoff and cursors unit-tested |
| Job-board APIs (Greenhouse, Lever, Ashby) | Tested | All three run live against public boards on a scratch database. The first live run showed the early-career filter was far too loose (it passed "President" and every "Program Manager"); it now matches whole words and returns real internships and new-graduate roles |
| Page watcher | Implemented | Tested with fixtures; depends on the page not being JavaScript-only |
| Guarded fetcher (SSRF, robots, caps) | Tested | Private addresses, redirects to private hosts, credentials, size caps, robots.txt |
| Classification, scoring, rank | Tested | Deterministic; property tests; live distribution inspected |
| Model assessment (Groq `gpt-oss-20b`) | Tested | Live: 120 items, 12 calls, 0 failures, $0.008; poison isolation and circuit breaker unit-tested |
| Briefs (Groq `gpt-oss-120b`) | Tested | Live: 11 briefs, $0.006; adapts sections to kind; refuses to explain a bare headline |
| Groq-only operation | Tested | Jev is switched off in `config/models.toml`: no client is built, the key is not read, no fit step runs, and stored Jev judgements are ignored by ranking. Unit tests pin each of these; a real cycle was run with every outgoing host recorded |
| Jev integration (dormant) | Tested | Kept in the codebase with its tests. Before being switched off it judged 30 items live with `jev-1.14.0` ($0.009) |
| Opportunity extraction and tracking | Partial | Rules path live (Codeforces). Model path run live on two real postings: fields came back sensibly, with "unknown" placeholders now normalised to empty. Not yet checked by hand on real internship postings with deadlines |
| Related items, timelines, resurfacing | Tested | Live: 119 relations on first run; same-story, citations and context boost unit-tested |
| Personalisation: levels, learning, feedback | Tested | Idempotence, undo, bounds, decay; "unread teaches nothing" is a test |
| Onboarding | Tested | In the browser: per-space rating, priorities, skippable |
| Source management, discovery, evaluation | Partial | Add, pause, approve, remove tested; link-based discovery tested; model-suggested discovery implemented but not yet run live |
| Web app | Tested | Every page checked in a real browser, light and dark, desktop and phone width; no console errors |
| Search | Tested | FTS5; hostile input safe |
| Notifications engine | Tested | Levels, gates, caps, one-per-story, freshness, quiet hours, reminders |
| Windows companion | Tested | A real toast was delivered and acknowledged on this machine; `install.ps1` itself has not been run (it changes the user's startup) |
| Auth (password, sessions, devices) | Tested | Unit and API tests; not yet exercised behind a real reverse proxy |
| Gmail (read-only) | Implemented | OAuth, query, parsing and refresh tested against fakes; **never connected to a real account** |
| Container image | Implemented | `Dockerfile` written; **not built or run here** (Docker is not installed on the build machine) |
| Cloud deployment | Blocked | `Dockerfile` and guide exist; deploying costs money and is the owner's decision |
| Cost tracking | Tested | Per call, per task; daily budget enforced |
| Continuous integration | Tested | `.github/workflows/tests.yml` runs the suite on every pull request targeting `main`: Ubuntu (Python 3.11, 3.12) and Windows (3.11), no secrets. Its first run found one environment-only failure (forced terminal colours in `--help` output), fixed in the workflow |

Automated tests: 868, no network, no keys.

Before publication (2026-11-16) an audit checked every tracked file and every commit: no API key value or
key-shaped string, no database or env file, no personal email, no machine path, and none of the private terms in
the owner's git-ignored profile. `tests/test_docs.py` keeps the file-level checks running.

### What the first real runs changed

Running against real services found things no unit test had, each now fixed and pinned by a test:

- job postings arrived with escaped HTML that survived as visible tags;
- the early-career filter matched fragments ("President", "Program Manager");
- a swarm of identically named spam repositories topped Home on popularity alone;
- a DNS hiccup on the laptop put five healthy sources on a multi-hour backoff;
- a first run notified about week-old items merely because they were newly acquired;
- routine contest listings were being marked Important;
- a "landmark" from a trusted source could not reach Critical.

## Open questions for the owner

They are the first table under Remaining work below, with what each one unlocks.

## Remaining work

Four groups, in the order they are worth doing. Nothing here blocks daily use on this computer.

### 1. Waiting on the owner

Decisions, configuration or credentials that only the owner can supply. Each has a default, so PIA runs meanwhile.

| What | Why it matters | Default until then |
|---|---|---|
| Review and merge pull request 1 (`platform-v2` into `main`) | `main` still holds v1 only | The branch is published; `main` is untouched |
| List the 15 to 20 organisations to monitor (Opportunities, with a careers or programme page each) | Unlocks the opportunities space | Codeforces contests only |
| Decide how wide the opportunities search is | Devpost and four aggregators are verified and waiting under Sources, Suggested | Off |
| Give a notices page for the university, or connect university mail | The University space has no source | Empty space |
| Authorise read-only Gmail (`pia gmail-connect`, see GMAIL.md) | University mail; the institute may block third-party access | Not connected |
| Review the starting interest profile on the Interests page | 43 topics were rated from the brief and old notes, unreviewed | Used as is |
| Decide on the 65 suggested sources | Includes high-volume general news left off to protect signal | Not followed |
| Run `companion\install.ps1` once | Desktop notifications on sign-in | Notifications appear in the app only |
| Choose hosting (OPERATIONS.md: about $3 to $6 a month) | Acquisition while the laptop is off; other devices | Runs on this computer |
| Say whether the interface meets the bar for polish | A matter of the owner's judgement | Not reviewed |

### 2. Real-world validation still owed

Built and covered by tests with fixtures or fakes, but not yet proven against the real thing. None needs new
design; each needs to be run once and looked at.

| What | How to validate | Depends on |
|---|---|---|
| Opportunity extraction on real internship postings | Run it on the first organisations' postings; check deadlines and eligibility by hand | Organisations chosen |
| Page watcher on a real careers or notices page | Add one through Opportunities and compare with the page | A page address |
| Adding a feed through the interface | Follow one new site from Sources, Add a source | Nothing |
| The reader (full text for arXiv, excerpt elsewhere) | Use "Fetch the text" on a paper and on an article | Nothing |
| Link-based source discovery | It runs daily; needs more history before it proposes anything | Time |
| Model-suggested source discovery and source evaluation | "Look for new sources" on the Sources page; judge what it proposes | Nothing (a few cents of Groq use) |
| "Subjects PIA has noticed" | Review the list on the Interests page with real data | Nothing |
| Clicking a desktop notification opens the item | Click one | Companion installed |
| The container image | Build and run it once | Docker, hosting decision |
| Auth behind a real HTTPS proxy | Sign in to the deployed instance; confirm the cookie is Secure | Deployment |
| Restoring from a backup | Rehearse once on a copy | Nothing |
| The ranking constants | A blind-labelled benchmark on a fresh sample (the v1 corpus is spent) | The owner's labelling time |

### 3. Gaps against the brief

Requirements that are partial or not implemented (COMPLETION.md has the detail). Smallest first.

| Gap | Size |
|---|---|
| Notes field for a tracked opportunity (the API already stores notes) | Small |
| Time-window filter in feeds (the API already accepts one) | Small |
| Change a source's fetch interval and spaces in the interface (API only today) | Small |
| Visiting a space as a learning signal | Small |
| Add or rename a space in the app (today: `config/taxonomy.toml` and `pia seed`) | Small |
| Fix the order-dependent v1 test (see limitations) | Small |
| Follow a researcher directly (for example arXiv by author) | Medium |
| Feedback that changes acquisition (propose pausing a source repeatedly marked poor) | Medium |
| Earlier developments written into briefs, and a single view of one development over time | Medium |
| Merge different spellings of one entity; relations between entities | Medium |
| Automated browser tests; a load test at tens of thousands of items | Medium |
| Alerting when the agent or many sources fail (today: the System page only) | Medium |
| Market data (prices, indices), if wanted | Medium, needs a data source decision |
| A reputation signal for sources that is not a model's opinion | Open question |

### 4. Later

Deferred in the PRD and still deferred: a mobile app and push, multi-user accounts, embeddings-based similarity,
other notification channels, an autonomous dossier system. Retire the v1 pipeline once a platform benchmark
supersedes the published evaluation.

## Known limitations and assumptions

- Ranking constants are named priors in `src/pia/intel/score.py`. No labelled data for the platform exists; the
  v1 benchmark corpus is spent and was not used to tune anything.
- A model's significance score is trusted only as one input (65% of importance), never as a threshold alone.
- Popularity is a percentile within each metric; sources with no public metric get a neutral value.
- Model-free mode cannot tell a spam repository from a good one except through the clone-swarm rule.
- About 1,230 items were still waiting for model assessment on 2026-11-16; the agent clears roughly 160 a cycle.
- Time zone for quiet hours defaults to India (UTC+5:30) and is a setting.
- One user. The schema has no user column; multi-user would be a migration.
- One v1 test is order-dependent: `tests/test_bench_cli.py::test_an_incomplete_arm_is_not_saved_unless_asked_and_can_be_resumed`
  fails if `tests/test_cli.py` runs before it in the same process (a logging handler left bound to a closed
  stream). It passes in the default order, locally and in CI, and was left alone rather than rewritten.
- The pull request is one squashed commit plus later commits on top. It was squashed once, on the owner's
  instruction, so that an earlier local version of one test file, which named a private detail, never left the
  machine.

## Handoff notes for the next session

- Branch `platform-v2`, pull request 1 into `main`, open and unmerged. Do not merge it or change `main` without
  the owner saying so. v1 code is untouched except: `models.py` (two optional fields), `llm/client.py` (records
  token usage), `cli.py` (registers platform commands; the old `web` command is gone; `--triage` now defaults to `llm`).
- Platform code: `src/pia/store`, `acquire`, `intel`, `notify`, `integrations`, `server`, plus `worker.py` and
  `platform_cli.py`. Tests: `tests/test_platform_*.py` with helpers in `tests/platform_helpers.py`.
- The real platform database is `data/platform.db` (created on first `pia serve`). `data/scratch/smoke.db` is a
  disposable test database from development and can be deleted.
- When writing files that contain backslash escapes, use an editor, not a shell heredoc (they get mangled).
- Do not tune ranking on the v1 benchmark labels.
- Groq is the only external model provider (ARCHITECTURE.md, A15). Do not switch Jev on, or add a provider,
  without the owner saying so. The switch is `[providers] jev` in `config/models.toml`.
