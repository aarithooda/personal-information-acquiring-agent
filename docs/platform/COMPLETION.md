# Product completion inventory

Every requirement of the original PIA brief, with its status on 2026-11-16. Built from the PRD, the roadmap,
the audit and the implementation; a few facts about what the interface exposes were checked in the code where
the documents did not say. Section numbers are the brief's own.

What comes next, in order, is in [ROADMAP.md](ROADMAP.md).

## Status words

| Status | Meaning |
|---|---|
| **Complete** | Built, covered by automated tests, and exercised for real where a real thing exists to exercise it against. |
| **Partial** | Works, with a stated gap between what the brief asked for and what exists. |
| **Awaiting validation** | Implemented and tested with fixtures or fakes, but not yet proven against the real service or by real use. |
| **Awaiting owner** | Built as far as it can be; blocked on the owner's configuration, credentials or decision. |
| **Not implemented** | Does not exist. |

## Summary

| Status | Count |
|---|---|
| Complete | 83 |
| Partial | 20 |
| Awaiting validation | 16 |
| Awaiting owner | 11 |
| Not implemented | 2 |

132 requirements in all. The two things not implemented are small: visiting a space is not used as a learning
signal, and the opportunities interface has no notes field (the API already stores notes). Nothing in the brief's
definition of the first release is missing. What is open is mostly waiting on the owner (organisations, hosting,
mail, the companion install) or on real use (reader, discovery, extraction on real postings, the ranking
constants).

## 4. Interest domains

| Requirement | Status | Evidence and what remains |
|---|---|---|
| The fifteen initial domains | Complete | Seeded from `config/taxonomy.toml`; each is a space with a feed. |
| Flexible; new domains and topics without architectural change | Partial | Domains and topics are rows. Topics can be added in the app. A new domain is added in `config/taxonomy.toml` and `pia seed`; there is no screen for adding or renaming a space. |
| Initial interest profile from the brief and existing personalisation data | Complete | 43 topics rated and space weights set from the brief and `config/interests.toml`. It is a starting point; the owner has not reviewed it yet. |

## 5. Core product experience

| Requirement | Status | Evidence and what remains |
|---|---|---|
| 5.1 Home: 5 to 15 featured items, not a feed of everything | Complete | Up to 12 unread items, at most 3 per space, one per story. |
| 5.1 Clear access to every domain space | Complete | Sidebar and the spectrum band. |
| 5.1 A personalised dashboard, not a news site | Complete | Featured by rank; closing-soon opportunities; onboarding prompt. |
| 5.2 A space and complete feed per domain | Complete | An item can appear in up to three spaces. |
| 5.2 Latest and Important views | Complete | |
| 5.2 Old items are not assumed stale; later developments can raise them | Complete | Rank never falls below 35% of an item's score; a related newer item lifts the older one ("Back in view"). Observed live. |
| 5.2 Nothing worthwhile is discarded for not fitting a top-N | Complete | Every acquired item stays in its feed. |
| 5.3 Unread, Read, Saved, Dismissed | Complete | |
| 5.3 States persist across sessions | Complete | Stored on the server. |
| 5.3 States persist across devices | Awaiting owner | Works for any browser that can reach the server; needs the hosting decision before a second device can. |
| 5.3 A dedicated Saved space | Complete | |
| 5.3 Dismissed items do not clutter; the owner controls their history | Complete | Hidden by default; a Dismissed filter restores them. |
| 5.4 Headline, summary, why it matters, key points, attribution, dates, original link, topics and domains | Complete | The item panel. A brief is written on first open and cached. |
| 5.4 Presentation adapts to the kind of material | Complete | Section guides per kind and space. Read live for papers and articles; the release, geopolitics, finance and opportunity guides have only been exercised by tests. |
| 5.4 Every item has a meaningful summary | Partial | Only assessed items have one. About 1,230 items were still waiting for assessment on 2026-11-16; the agent clears roughly 160 a cycle. Title-only items never get an invented summary. |
| 5.5 Paper metadata and abstracts | Complete | |
| 5.5 Full text where legitimately accessible | Awaiting validation | arXiv's HTML rendering and guarded page fetching are implemented and tested with fixtures; not yet exercised against live pages. |
| 5.5 An in-platform reader or detailed explanation | Awaiting validation | The brief is proven live. The reader ("Fetch the text") has not been used on a real page. |
| 5.5 Citations and references preserved | Partial | The original is always linked. Items that cite another stored item by URL or arXiv id are linked; reference lists are not extracted. |
| 5.5 Respect paywalls and copyright | Complete | Excerpt only for non-open sources; robots.txt honoured; full text shown only for open-access papers. |
| Search across what was acquired | Complete | FTS5 over titles, source text and summaries. |
| Filter what was acquired | Partial | By state and kind. There is no time-window filter in the interface (the API accepts one). |

## 6. Acquisition and source discovery

| Requirement | Status | Evidence and what remains |
|---|---|---|
| 6.1 Follow websites, organisations, labs, journals, company blogs, announcement pages | Awaiting validation | "Add a source" finds a feed, a job-board API or a page to watch. Job boards were inspected live; adding a feed through the interface has only run against fixtures. |
| 6.1 Follow researchers | Partial | A researcher can be followed as a topic (their name is matched in new items) or through their own blog feed. There is no author-level adapter (for example arXiv by author). |
| 6.1 Follow newsletters | Partial | Newsletters with a feed (for example Substack) work. Email-only newsletters would need the mail integration. |
| 6.1 Manage sources through the interface | Complete | Follow, pause, resume, remove, approve, decline. Fetch interval and space assignment can be changed through the API only. |
| 6.2 Discovery is separate from monitoring | Complete | Discovery only creates candidates; only active sources are fetched. |
| 6.2 Discover sources from existing sources and acquired information | Awaiting validation | Finds sites that well-ranked items keep linking to. It runs daily and has run live, but has not yet met its threshold, so no suggestion has come from it. |
| 6.2 Discover sources from interests, organisations, researchers, technologies, emerging subjects | Awaiting validation | A model proposes sources for the owner's top interests and each must verify as a real feed. Implemented; never run live. It works from interests, not yet from named organisations or researchers. |
| 6.3 Evaluate relevance, quality, signal-to-noise, consistency, added coverage | Awaiting validation | Measured from the feed itself: recency, frequency, text, interest match, overlap with what already arrives. Tested with fixtures; not yet seen on a live discovered source. |
| 6.3 Evaluate reliability, credibility, reputation, provenance | Partial | A model's opinion from the source's name and recent headlines, shown and labelled as an opinion. No independent reputation data. |
| 6.3 Recommendations explain what, why discovered, why worth following | Complete | 65 catalogue suggestions are waiting with their reasons. |
| 6.3 The owner decides what is trusted or monitored | Complete | |
| 6.3 Deciding on the 65 waiting suggestions | Awaiting owner | Sources page, Suggested. |
| 6.4 News, papers, lab and company announcements, model and software releases, robotics, geopolitics, economics | Complete | 77 active sources; live run fetched 76 of 77. Labs with no feed (Anthropic, Meta AI) arrive only through Hacker News. |
| 6.4 Financial and market developments | Partial | News, central-bank releases, research and commentary. No market data (prices, indices). |
| 6.4 Institutional announcements | Awaiting owner | No university source: the site could not be reached to verify one. Needs a page address or the mail integration. |
| 6.4 Research and career opportunities | Awaiting owner | Only contest listings are active until organisations are chosen. |
| 6.4 Prefer APIs and feeds; no fragile scraping | Complete | |
| 6.4 Document coverage, reliability and failure behaviour | Complete | `SOURCES.md`. |

## 7. Personalisation and learning

| Requirement | Status | Evidence and what remains |
|---|---|---|
| 7.1 Onboarding: candidate topics in manageable groups, three levels, a handful marked most important | Complete | Interests page, one space at a time, with Priority. |
| 7.1 The platform is usable before onboarding is finished | Complete | Nothing is gated; unrated topics are neutral. |
| 7.2 Follow, mute, prioritise, adjust, inspect and edit the profile | Complete | |
| 7.2 Discover related subjects; occasionally recommend new ones | Awaiting validation | "Subjects PIA has noticed" lists model-extracted topics seen on several items. Tested; not yet reviewed with real data. |
| 7.2 Personalisation is not opaque | Complete | Levels, learned values with their signals, and a per-item "why you are seeing this". |
| 7.3 Learn from saving, reading, opening, following, dismissing, likes and dislikes, quality feedback | Complete | An explicit rating always outranks learned behaviour. |
| 7.3 Learn from exploring particular domains | Not implemented | Visiting a space is not recorded as a signal. |
| 7.3 Explicit feedback with the five options and an optional reason | Complete | With undo. |
| 7.3 Unread or unopened is never treated as dislike | Complete | Pinned by a test. |
| 7.3 Feedback improves ranking and recommendations | Complete | |
| 7.3 Feedback improves future acquisition | Partial | "Poor quality" lowers that source's items. It does not change what is fetched or propose dropping the source. |

## 8. Opportunities intelligence

| Requirement | Status | Evidence and what remains |
|---|---|---|
| A dedicated opportunities space | Complete | |
| Cover internships, research programmes, fellowships, summer schools, hackathons, competitions, developer programmes, university and company programmes | Awaiting owner | The model and extraction cover every type. Only Codeforces contests are active. |
| Emphasis on first-year eligibility, other opportunities kept | Complete | A field, a filter, a relevance bonus and a notification rule. |
| 8.1 Monitor only a curated list of organisations; editable later | Complete | Add and remove organisations and their pages in the app. |
| 8.1 Monitor career, internship, research, fellowship, developer-programme and announcement pages | Awaiting validation | Greenhouse, Lever and Ashby boards ran live. The page watcher is tested with fixtures only and cannot read pages rendered entirely by JavaScript. |
| 8.1 The organisation list itself | Awaiting owner | None invented; no broad search switched on. |
| 8.2 Extract organisation, programme, type, eligibility, year requirements, location and mode, deadline, start and duration, qualifications, link, source and date | Awaiting validation | Rules ran live on contests; the model ran on two real postings. Not yet checked by hand on real internship postings with deadlines. |
| 8.2 Deadlines and eligibility prominent | Complete | |
| 8.2 Expired opportunities never shown as active | Complete | Derived from the date at read time. |
| 8.3 Save and track with statuses | Complete | Interested, Planning to apply, Applied, Accepted, Rejected, plus a derived Closed. |
| 8.3 Notes on a tracked opportunity | Not implemented | The database and API store notes; the interface has no field for them. |
| 8.4 Read-only university mail: architecture, permissions, security | Awaiting validation | Implemented with the single `gmail.readonly` scope; tested against fakes; never connected to a real account. |
| 8.4 Authorising the mailbox | Awaiting owner | `GMAIL.md`. The university may block third-party access. |

## 9. Proactive notifications

| Requirement | Status | Evidence and what remains |
|---|---|---|
| Three levels: Interesting, Important, Critical | Complete | |
| Configurable thresholds; no repetitive flooding | Complete | Thresholds, daily caps, quiet hours, one per story, recent items only. |
| Selectivity never decides retention | Complete | |
| Windows desktop notifications that arrive naturally | Awaiting owner | A real notification was delivered and acknowledged on the owner's machine. `companion\install.ps1` has to be run once by the owner. |
| A notification explains why and opens the item | Awaiting validation | The reason is in the notification and the link targets the item; clicking through from a real toast has not been checked. |
| Acquisition continues when the laptop is off | Awaiting owner | Hosting decision. PIA currently runs on the owner's computer. |
| Cloud backend, web app, lightweight companion with no local model | Awaiting validation | The architecture is in place and the companion is a polling script. The container image is written but has never been built or run. |

## 10. Connected information

| Requirement | Status | Evidence and what remains |
|---|---|---|
| Related-item discovery | Complete | Same story, citation, shared entities and topics. Ran live. |
| Topic timelines | Complete | Every topic and entity has one. |
| Development histories | Partial | A timeline plus earlier and later connected items. No single view of one development across weeks. |
| Relationships between organisations, researchers, technologies and topics | Partial | Entities are extracted and linked to items. There are no entity-to-entity relations. |
| Contextual references to earlier developments | Partial | Shown as connected items; not woven into the written brief. |
| No elaborate knowledge graph as a prerequisite | Complete | |

## 11. Excluded from scope

| Requirement | Status | Evidence and what remains |
|---|---|---|
| No chatbot, no conversational Q&A, no on-demand research agent | Complete | None exists. Models only assess, brief, extract and evaluate. |

## 12. AI and processing architecture

| Requirement | Status | Evidence and what remains |
|---|---|---|
| Verify the actual implementation, providers and model identifiers | Complete | `AUDIT.md`. |
| Evaluate the roles of Jev and Groq on evidence; no unsupported superiority claims | Complete | The owner then chose Groq-only operation; Jev is kept, switched off (`ARCHITECTURE.md`, A15). |
| Deterministic processing wherever it avoids model calls | Complete | Classification, deduplication, topic matching, popularity, relations and ranking are code. |
| Classification, relevance, importance, topic extraction, summarisation, personalisation | Complete | |
| Deduplication | Complete | Canonical URL, plus same-story linking. |
| Entity resolution | Partial | The same name becomes the same entity. Different spellings of one entity are not merged. |
| Contextual explanation | Partial | "Why it matters" uses the owner's stage and interests, not the item's history. |
| Source evaluation by a model | Awaiting validation | Not yet run live. |
| Opportunity extraction by a model | Awaiting validation | See 8.2. |
| Not every item goes through an expensive model; routing by task | Complete | A cheap batched model for volume, the stronger one only for briefs and extraction, a per-cycle budget. |
| Track model usage and cost | Complete | Per call and per task, with a daily budget. |
| Providers replaceable | Partial | Behind one small interface with prices in configuration. Not exercised with a second LLM provider. |

## 13. Technical and architectural areas

| Requirement | Status | Evidence and what remains |
|---|---|---|
| Acquisition and source adapters; normalisation; storage | Complete | |
| Scheduling and background workers | Complete | In-process agent and `pia run-once`; both ran live. |
| Ranking and recommendation | Awaiting validation | Works and is tested. Its constants are stated priors: no labelled evaluation of the platform's ranking exists. |
| Web application | Complete | |
| Authentication and user security | Awaiting validation | Password, sessions, device tokens, CSP, origin and host checks are tested. Not yet exercised behind a real HTTPS proxy. |
| Cloud deployment | Awaiting owner | Options and costs are in `OPERATIONS.md`. |
| Monitoring, logging and observability | Partial | System page: run history, source health, backlog, cost. No external alerting and no log shipping. |
| Cost management | Complete | |
| Backups and data recovery | Partial | Daily snapshots and a copy before any schema upgrade, both exercised. Restoring is documented but has not been rehearsed. |
| A coherent architecture without needless complexity | Complete | One process, one database file, one static web app. |

## 14. Interface and design quality

| Requirement | Status | Evidence and what remains |
|---|---|---|
| Home hierarchy, domain navigation, cards and detail, Latest and Important, saved, read and unread states, feedback controls | Complete | Checked in a real browser, light and dark, desktop and phone width. |
| Opportunities dashboard, source management, personalisation settings, notification settings | Complete | |
| Search and filters | Partial | See section 5: no time-window filter. |
| Polished, modern, consistent | Awaiting owner | A matter of the owner's judgement; not yet reviewed by them. |
| Built, run, inspected and refined in a browser | Complete | There are no automated browser tests; the pure logic is tested with Node. |

## 15 and 16. How the work was to be done

| Requirement | Status | Evidence and what remains |
|---|---|---|
| Audit of the existing system; existing data preserved | Complete | `AUDIT.md`; v1's database was only read. |
| PRD separating agreed, proposed, open and deferred | Complete | `PRD.md`. |
| Architecture, decisions, roadmap, limitations and assumptions | Complete | `ARCHITECTURE.md`, `ROADMAP.md`. |
| Meaningful tests for the listed behaviours | Complete | 868 tests. |
| Tests run automatically on pull requests | Complete | `.github/workflows/tests.yml`, on Ubuntu and Windows. |
| Performance under realistic workloads | Partial | Observed at about 1,800 items (one slow page was found and fixed). No load test. |
| Frontend behaviour verified | Partial | By hand in a browser and by logic tests; no automated end-to-end tests. |
| Implemented, tested, partial and blocked kept distinct | Complete | This document and `ROADMAP.md`. |
| A precise handoff | Complete | `ROADMAP.md`. |

## 17. Definition of success for the first release

| Requirement | Status |
|---|---|
| A working web application | Complete |
| A useful homepage | Complete |
| Separate domain information spaces | Complete |
| Latest and Important views | Complete |
| Persistent information states | Complete |
| Reliable information acquisition | Complete |
| Source management | Complete |
| A functioning processing and ranking pipeline | Complete |
| An initial personalisation system | Complete |
| Explicit user feedback | Complete |
| Search and saved information | Complete |
| A dedicated opportunities space | Complete |
| The foundations for selective organisation monitoring | Complete |
| A notification architecture | Complete |
| Clear setup and operational documentation | Complete |

The capabilities the brief listed as following on (advanced discovery, deeper paper exploration, cloud
scheduling, Windows notifications, Gmail, connected information) are each built; their status is in the
sections above, and most are waiting on real use or on the owner.
