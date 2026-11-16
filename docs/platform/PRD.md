# PIA platform: product requirements

PIA (Personal Information Acquiring Agent) is a continuously operating, personalised information intelligence
platform for one person. It discovers, acquires, evaluates, organises and presents developments across the
owner's interest domains, learns what they care about, and proactively tells them when something matters.

It is **not** a generic news aggregator, an RSS reader, or a chatbot.

Status key used throughout: **[A]** agreed requirement (from the owner's brief), **[P]** proposed implementation
choice (made by engineering, changeable), **[Q]** open question for the owner, **[D]** deferred.

## 1. Domains

[A] Fifteen initial domains, flexible and extensible without architectural change: AI and ML; computer science and
software engineering; mathematics; physics; robotics; quantitative finance and markets; economics and business;
geopolitics; startups and technology companies; general science and technology; research opportunities and
internships; hackathons, competitions and developer programmes; university announcements; cybersecurity and
systems; space and astrophysics.

[P] Domains are rows, not code. Each has a slug, a name, classification hints and a personal weight. The
university domain's institution name and other personal details live in a git-ignored local file
(`config/personal.toml`), because the repository is public.

[P] Topics are a flat, evolving set (no deep hierarchy). A topic belongs to at most one domain, can be created by
the owner, by seeding, or proposed by the system.

## 2. Experience

### Home
[A] A personalised dashboard, not a feed of everything: about 5 to 15 featured items, plus clear access to every
domain space.
[P] Featured items are the top unread items by rank score from the recent window, with a per-domain cap so one
domain cannot fill the page. The page also shows per-domain unread counts, upcoming opportunity deadlines and the
agent's last activity.

### Domain spaces
[A] Every domain has its own space and complete feed, with two views: **Latest** (chronological) and
**Important** (ranked by significance and personal relevance). Old items are not assumed stale: ranking accounts
for context, and a later development can raise an earlier item. Items outside any top-N remain in the feed.
[P] Important applies a slow time decay (half-life 21 days) and a "context boost" when new items relate to an
older one. Filters: unread only, item kind, time window.

### Item states
[A] Unread, Read, Saved, Dismissed; persistent across sessions and devices; a dedicated Saved space; dismissed
items stay out of the normal experience; the owner keeps control of their history.
[P] Stored server-side as independent timestamps (`read_at`, `saved_at`, `dismissed_at`), so a saved item can be
read or unread. Dismissed items are hidden by default and recoverable from History.

### Item presentation
[A] Headline, meaningful summary, why it matters, key findings or implications, source attribution, publication
and acquisition dates, original link, topic and domain associations. The template adapts to the material (paper,
model release, software release, geopolitical or financial development, opportunity).
[P] Two tiers of text. Every assessed item gets a one or two sentence summary. A full structured brief (sections
chosen by item kind) is written by the stronger model for featured items and on first open of any other item,
then cached.

### Deeper exploration
[A] Where access and terms permit: paper metadata, abstracts, legitimately accessible full text, an in-platform
reader or detailed explanation, citations preserved. Paywalled or restricted sources: metadata and permitted
excerpts only, with the original always linked.
[P] arXiv papers: abstract and metadata from the API, full text from arXiv's own HTML rendering when it exists.
Other pages: fetched on demand through a guarded fetcher (no private addresses, robots.txt respected, size and
time caps), kept as a private analysis cache and shown only as a short excerpt beside the brief.

### Search
[A] Browse, search, filter, save and explore what has been acquired.
[P] Full-text search (SQLite FTS5) over titles, source text and summaries, filterable by domain, kind and state.

## 3. Acquisition and sources

[A] Three complementary mechanisms: sources the owner follows explicitly; autonomous discovery of new sources;
evaluation and recommendation of discovered sources with the owner retaining oversight. Discovery and monitoring
are separate; nothing discovered is silently trusted. Prefer APIs, feeds and structured pages over scraping.
Document coverage, reliability and failure behaviour.

[P] One `sources` table with a lifecycle: `candidate -> active <-> paused`, or `rejected`. Only `active` sources
are monitored. Discovery produces candidates with an evaluation and an explanation; the owner approves them.
[P] Adapters: RSS/Atom, arXiv, Hacker News, GitHub, HF Daily Papers, JSON APIs with a declared mapping
(hackathon and contest listings), a page watcher for organisations with no feed (opportunity monitoring only),
and Gmail (read-only, off until authorised).
[P] Adding a source in the UI takes a site or feed URL; PIA finds the feed, previews it and evaluates it first.
[P] Discovery channels: a vetted catalogue matched against the interest profile; sites that well-received items
keep linking to; model-suggested sources that are verified by actually fetching them before being shown.

## 4. Personalisation

[A] An evolving, inspectable, correctable model of interests. Onboarding that presents manageable groups of
candidate topics to classify as highly interesting, somewhat interesting or not interesting, with a way to mark a
handful as most important; never a gate on using the product. Follow, mute, prioritise, discover and adjust
topics. Learn from saves, reads, opens, domain exploration, follows, dismissals and explicit feedback. Explicit
feedback options (liked, useful, not relevant, poor quality, not interested in this topic) with an optional
reason. Unread or unopened never counts as dislike.

[P] Each topic has an explicit level (set by the owner) and a learned affinity (accumulated from behaviour, bounded,
decaying). The profile page shows both and what produced the learned part; anything can be overridden or reset.
[P] The initial profile is seeded from the brief and from the existing `config/interests.toml`.

## 5. Opportunities

[A] A dedicated space, not buried in feeds: internships, research programmes, fellowships, summer schools,
hackathons, competitions, developer programmes, university announcements, selected company programmes. First-year
undergraduate eligibility matters most; other relevant opportunities are kept. Selective monitoring of an
owner-curated list of roughly 15 to 20 organisations (career, internship, research, fellowship, developer
programme and announcement pages), editable later. Extract organisation, programme, type, eligibility, year
requirements, location and mode, deadline, start and duration, qualifications, application link, source and date.
Deadlines and eligibility prominent; never present expired opportunities as active. Tracking statuses.

[P] Statuses: Interested, Planning to apply, Applied, Accepted, Rejected, plus an automatic Expired/Closed
display state derived from the deadline (it never overwrites what the owner recorded).
[A] The monitoring infrastructure is built now; **no organisation list is invented and no broad opportunity
search is switched on** until the owner supplies preferences.
[Q] Which organisations to monitor (see OPEN_QUESTIONS in the roadmap).

### University mail
[A] Read-only Gmail integration for the university account as a possibility; no access, credentials or
modification without explicit authorisation.
[P] OAuth 2.0 with the single scope `gmail.readonly`, tokens stored locally, a sender and keyword filter so only
announcement-like mail is read. Implemented and tested against fakes; switched off until the owner authorises.

## 6. Notifications

[A] Three levels: Interesting, Important, Critical; all supported; configurable thresholds; no repetitive
flooding. Notification selectivity never decides retention. Windows desktop notifications that open the item in
PIA. Acquisition continues when the laptop is off: cloud backend, web app as the primary UI, lightweight Windows
companion with no local model.

[P] The server decides and records notifications; clients only deliver. The companion polls with a device token
and shows native toasts. Controls: minimum level per channel, per-level daily caps, quiet hours, one notification
per story.

## 7. Connected information

[A] Related-item discovery, topic timelines, development histories, relationships between organisations,
researchers, technologies and topics, contextual references to earlier developments. No elaborate knowledge graph
as a prerequisite.
[P] Entities are topics of kind `entity`. Relations are computed deterministically (shared topics and entities,
title similarity, explicit links between items) and stored with a kind and a score. Topic pages are timelines.

## 8. Processing and models

[A] Jev and Groq stay in architectural consideration; roles decided on evidence; no unsupported superiority
claims. Tasks: classification, relevance, importance, topic extraction, deduplication, summarisation, contextual
explanation, source evaluation, opportunity extraction, personalisation. Not every item goes through expensive
models. Track usage and cost. Keep providers replaceable.

[A] **Groq is the only external model provider** (owner's decision, 2026-11-16). Jev stays in the codebase,
switched off, for possible future use.

[P] See ARCHITECTURE.md, "Model routing". In short: deterministic first; batched `gpt-oss-20b` for assessment;
`gpt-oss-120b` only for full briefs, opportunity extraction and source evaluation, all on Groq. The system
degrades to deterministic operation with no keys at all.

## 9. Explicitly out of scope

[A] No general-purpose chatbot, no conversational follow-up Q&A, no on-demand research agent.

## 10. Deferred

[D] Mobile app and push; multi-user accounts; embeddings-based similarity; an autonomous dossier system; other
notification channels (email, Telegram); a new labelled benchmark for the platform's ranking.

## 11. Definition of the first release

A working web app with home, domain spaces (Latest and Important), persistent states, reliable acquisition, source
management, a processing and ranking pipeline, initial personalisation with onboarding, explicit feedback, search
and saved items, an opportunities space with organisation monitoring foundations, a notification architecture,
and setup and operations documentation. What is implemented, tested, partial or blocked is tracked honestly in
[ROADMAP.md](ROADMAP.md).
