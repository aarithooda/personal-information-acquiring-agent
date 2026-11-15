# Roadmap, status and handoff

The single place that says what is done, how well it is verified, and what comes next. Updated 2026-11-16.

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

These are the decisions engineering cannot make. Each has a provisional default so nothing is blocked on it.

1. **Organisations to monitor for opportunities** (the 15 to 20 you mentioned). Default: none; the space shows
   Codeforces contests only. Add them under Opportunities with a careers or programme page address.
2. **How wide the opportunities search should be.** Devpost hackathons and four opportunity aggregators are
   verified and waiting under Sources, Suggested. Default: off.
3. **University announcements.** The institute's website could not be reached from here to verify a feed or a
   notices page. Options: give a page address to watch, or connect university mail (`pia gmail-connect`).
   Default: the space exists but has no source.
4. **Topic ratings.** The starting profile is derived from your brief and `config/interests.toml` (43 topics
   rated). The Interests page is the questionnaire; anything unrated is neutral.
5. **Which suggested sources to follow.** 65 are waiting, including high-volume general news (TechCrunch, Ars
   Technica, BBC World, Al Jazeera) that was left off to protect signal.
6. **Hosting.** See OPERATIONS.md for options and rough costs. Default: runs on this computer.
7. **Merging.** The branch `platform-v2` is published as a single commit (squashed on the owner's instruction so
   that an earlier local version of one test file, which named a private detail, never left the machine). It
   has not been merged into `main`; that is the owner's call, by pull request when ready.

## Known limitations and assumptions

- Ranking constants are named priors in `src/pia/intel/score.py`. No labelled data for the platform exists; the
  v1 benchmark corpus is spent and was not used to tune anything. A new benchmark is the right next evidence step.
- A model's significance score is trusted only as one input (65% of importance), never as a threshold alone.
- Popularity is a percentile within each metric; sources with no public metric get a neutral value.
- Model-free mode cannot tell a spam repository from a good one except through the clone-swarm rule.
- Time zone for quiet hours defaults to India (UTC+5:30) and is a setting.
- One user. The schema has no user column; multi-user would be a migration.

## Next steps, in order

1. Owner answers the open questions above (organisations first: it unlocks the opportunities space).
2. Run model-suggested source discovery live and review the quality of what it proposes.
3. Run opportunity extraction live on real postings once organisations are chosen; check deadlines by hand.
4. Deploy (after the hosting decision), then install the companion against the deployed address.
5. Connect university mail, if wanted.
6. Build a platform benchmark: blind labels on a fresh sample, to test the ranking constants.
7. Retire the v1 pipeline once that benchmark supersedes the published one.

## Handoff notes for the next session

- Branch `platform-v2`. v1 code is untouched except: `models.py` (two optional fields), `llm/client.py` (records
  token usage), `cli.py` (registers platform commands; the old `web` command is gone; `--triage` now defaults to `llm`).
- Platform code: `src/pia/store`, `acquire`, `intel`, `notify`, `integrations`, `server`, plus `worker.py` and
  `platform_cli.py`. Tests: `tests/test_platform_*.py` with helpers in `tests/platform_helpers.py`.
- The real platform database is `data/platform.db` (created on first `pia serve`). `data/scratch/smoke.db` is a
  disposable test database from development and can be deleted.
- When writing files that contain backslash escapes, use an editor, not a shell heredoc (they get mangled).
- Do not tune ranking on the v1 benchmark labels.
- Groq is the only external model provider (ARCHITECTURE.md, A15). Do not switch Jev on, or add a provider,
  without the owner saying so. The switch is `[providers] jev` in `config/models.toml`.
