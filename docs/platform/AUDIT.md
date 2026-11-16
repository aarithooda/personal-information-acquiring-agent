# Existing-system audit (2026-11-15)

What PIA was before the platform redesign, verified against the code, the real database and a full test run. This
is the baseline the decisions in [ARCHITECTURE.md](ARCHITECTURE.md) are made from.

## What existed

PIA v1 was a **terminal briefing tool**: "what happened since I last checked?". One command collected from nine
sources, triaged the new items with a model, had an editor model pick headlines, rendered Markdown and moved a
checkpoint. A small local web UI (three tabs) displayed past briefings and favourites.

| Area | State on audit | Verdict |
|---|---|---|
| Tests | 754 passed in 73 s, no network (a socket guard enforces it) | Healthy; a real asset |
| HTTP layer (`pia.http`) | One retrying request function; errors never leak headers or keys | **Keep** |
| URL canonicalisation (`pia.normalize`) | Deterministic, arXiv-aware, strips trackers | **Keep** |
| Source adapters (`pia.sources`) | arXiv, HF Daily Papers, Hacker News, GitHub, generic RSS; each a pure parser plus a thin fetch | **Keep and extend** |
| Source registry | A static TOML file, 9 sources, AI and software only | **Replace** with a database-backed registry the UI can manage |
| Storage (`pia.db`, `pia.state`) | SQLite, 4 migrations; item lifecycle is `discovered -> enriched -> briefed/skipped` | **Replace**: the lifecycle encodes "a briefing consumed this item", which has no place in continuous feeds |
| Groq client (`pia.llm.client`) | OpenAI-compatible chat API over httpx, strict JSON-schema output, error taxonomy | **Keep** |
| Stage 1 LLM triage (`pia.llm.enrich`) | `openai/gpt-oss-20b`, batched, 4 categories, importance 1-5, one-line summary | Idea kept, implementation replaced (15 domains, kinds, topics) |
| Stage 2 editor (`pia.llm.headlines`) | `openai/gpt-oss-120b` picks and explains headlines from a shortlist | Replaced by per-item adaptive briefs |
| Jev client (`pia.jev.client`) | Typed client for TypeSafe's decision model (`POST api.typesafe.ai/v1/systemone`) | **Keep** |
| Jev triage v2 (`pia.jev.design`) | 69 atomic questions per item in 4 requests against a TOML profile | **Keep**, switched off: the owner later chose Groq-only operation (ARCHITECTURE.md, A15) |
| Interest profile (`pia.profile`) | A hand-written TOML file (git-ignored), hashed into results | Kept as an input; the live profile moves to the database |
| Ranking (`pia.briefing.rank`) | model score + per-source popularity percentile + cross-source boost | Ideas kept (percentiles, no absolute cutoffs), formula redesigned |
| Web UI (`pia.web`) | Vanilla JS, 3 tabs over rendered briefings, favourites, loopback-only | **Replace** |
| Scheduling | None: runs only when a `.bat` file is double-clicked | **Add** |
| Benchmarks (`benchmarks/`, `benchmark_v2/`) | Blind-labelled evaluations with a frozen methodology | **Keep untouched** as the evidence record |

The real database (`data/pia.db`, schema v4) held 500 items collected 20-26 September 2026 (211 Hacker News, 114
arXiv, 80 HF papers, 60 GitHub, 35 lab blogs), 500 triage rows, 4 briefings and no favourites. Nothing has been
collected since: with no scheduler the product stopped the day it stopped being launched by hand.

## Verified provider facts

- **Groq** is the inference provider (`https://api.groq.com/openai/v1/chat/completions`). Models in code:
  `openai/gpt-oss-20b` (triage) and `openai/gpt-oss-120b` (editor). Not to be confused with xAI's Grok; nothing in
  the repository uses Grok.
- **Jev** is TypeSafe's hosted "System One" decision model (alias `jev-latest`; every stored answer came from
  `jev-1.13.0`). It answers typed questions (yes/no probability, choice, score) about a JSON state. It writes no
  text, so it cannot summarise or extract.
- Both keys are present locally (`GROQ_API_KEY`, `JEV_API_KEY`) in a git-ignored env file.

## What the evidence says about Jev and the LLM triage

From `docs/evaluation.md` (benchmark v2, 160 fresh items, blind labels, each system run once):

| System | nDCG@16 | AP (lenient) | AUC (lenient) |
|---|---|---|---|
| Jev v2 | 0.362 | 0.375 | 0.627 |
| LLM Stage 1 (`gpt-oss-20b`) | 0.316 | 0.387 | 0.670 |
| Jev v1 | 0.392 | 0.396 | 0.650 |
| Popularity only (no model) | 0.335 | 0.315 | 0.561 |

Jev v2 ranks above chance. It is **not** shown to be better than the LLM triage, than Jev v1, or (on these
intervals) clearly better than popularity alone. All three model systems under-rank title-only items. The corpus
is spent: it must not be used to tune anything.

Cost and shape, measured in the design log: Jev v2 costs about 6.5k input tokens and four requests per item
(roughly $0.0003 and 1.6 s per item) and returns only numbers. A batched `gpt-oss-20b` call handles about ten
items per request and returns a category, an importance and a summary together.

## Shortcomings against the product vision

1. **Scope**: 4 categories, AI and software sources only. The brief names 15 domains.
2. **No continuity**: no scheduler, no background operation, no notifications.
3. **Briefing-centred data model**: items are consumed by briefings; an item outside the top few is "skipped"
   and effectively lost. The brief requires every worthwhile item to stay browsable.
4. **No item states** beyond a favourite flag: no read, unread or dismissed.
5. **Personalisation is a static file**: no onboarding, no learning from behaviour, no feedback, not inspectable
   in the product.
6. **Source management is a text file**; no discovery, evaluation or recommendation of sources.
7. **No opportunities model**, no deadlines, no tracking.
8. **No search** over acquired information beyond a title filter in the old Library tab.
9. **No relationships** between items.
10. **Title-only items** (58.6% of v1's items, mostly Hacker News) get thin treatment because nothing fetches text.
11. **No auth, no deployment story**: loopback-only by design.
12. **Model usage is not tracked** as a running cost, only noted in the design log.

## What is preserved

- All of v1's data: the 500 items, their triage results and signals are imported into the new store by
  `pia import-legacy` (read-only on the old file, which is never modified).
- The v1 briefing pipeline and both benchmarks remain in the repository, runnable and tested, as the frozen system
  the published evaluation describes. They are no longer the product; see ARCHITECTURE.md, decision A2.
