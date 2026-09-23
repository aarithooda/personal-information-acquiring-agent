# Source coverage: frontier AI labs

Why PIA watches the sources it watches, evaluated 2026-09-23, prompted by a real miss: an Anthropic
model announcement ("Opus 5.5") did not reach a briefing because PIA has no Anthropic source, and the
run that was expected to find it never actually executed (see "Why the original incident happened"
below — it was not a source, ranking or model problem).

This file is about the **input layer only**. It does not change Jev, the ranking formula, the editor,
or how a source is turned into a briefing item; every source here feeds the same
`collect → normalize → dedupe → checkpoint → Jev → rank → editor → briefing` pipeline described in the
README.

## Rule for adding a source

Add a source only if it has: (1) substantial relevance to the reader's stated interests, (2)
meaningful frontier-model/research/software activity, (3) a **reliable official** publication
mechanism, (4) a stable way for PIA to collect it without scraping, and (5) a reasonable
signal-to-noise ratio. Preference order: official RSS/Atom > official structured feed/API > a stable
official announcement index > a small dedicated adapter > scraping (last resort, and not done here).

## Added

| Source | Mechanism | Adapter | Why |
|---|---|---|---|
| `google_ai` | Official RSS: `https://blog.google/innovation-and-ai/technology/ai/rss/` | Generic `rss` (config only, no new code) | Google's own AI-category blog: Gemini app, API and platform announcements. Complements the existing `deepmind` source (DeepMind's research blog, which also carries some Gemini model news) with the product/platform side. Verified as a real, well-formed RSS feed, distinct from Google's noisy site-wide `blog.google/rss/` (all of Google, not just AI). |

Both `deepmind` and `google_ai` are official Google sources, so between them "Google / Gemini" has
first-class coverage from the research and product sides, without adding a dedicated adapter.

## Evaluated and NOT added (no reliable official feed)

For each, I checked (a) common RSS/Atom paths, (b) `<link rel="alternate" type="application/(rss|atom)+xml">`
tags on the homepage, and, where a company publishes open-weight models, (c) whether their model
repository's GitHub Releases could serve as an official structured proxy.

| Provider | What I checked | Result |
|---|---|---|
| **Anthropic / Claude** | `/rss.xml`, `/news/rss.xml`, `/feed(.xml)`, `/index.xml`, `/engineering/rss.xml`, a docs release-notes page, `<link>` tags on `/`, `/news`, `/research` | No feed anywhere; the site is a JS-rendered SPA with nothing to discover. A sitemap (`/sitemap.xml`) exists and does list announcement pages (it currently lists `claude-opus-5-5`), but it is a flat, mostly undated catalogue mixed with legal, careers and product-marketing pages — not a chronological announcement feed, and most entries carry no `<lastmod>`. Using it would mean diffing a big URL list and then fetching each new page to scrape a title, which is exactly the "brittle scraping" this project avoids. GitHub release feeds for Anthropic's SDKs (e.g. `anthropic-sdk-python`, `claude-code`) exist, but they track **library version bumps**, not model or product announcements — they would not have caught "Opus 5.5" either. **Not added.** Reachable today only indirectly, via Hacker News if a story clears `min_points`. |
| **Groq** | `/rss.xml`, `/blog/rss.xml`, `/feed(.xml)`, `/index.xml`, `/atom.xml`, `<link>` tags on `/` | No feed; JS-rendered SPA, no discoverable alternative. **Not added.** |
| **Z.ai / GLM (Zhipu)** | `/rss.xml`, `/blog/rss.xml`, `/feed`, `docs.z.ai/rss`, `open.bigmodel.cn/rss` and `/news/rss.xml` | The `bigmodel.cn` candidates return HTTP 200, but for *every* path (a single-page app returning its shell regardless of URL) — not real feeds. The natural GitHub alternative, the `GLM-*` model repos' Releases feed, exists but is **empty** for the current model repo (releases are not consistently tagged there) and is tied to a version-specific repository name that would need updating by hand every model generation — not a stable mechanism. **Not added.** |
| Meta AI, Mistral AI, xAI, Cohere, AI21 Labs, Microsoft AI, DeepSeek, Alibaba/Qwen | Common RSS/Atom paths per provider | No working feed found for any of them in this pass (SPA shells, docs-site catch-all routes returning 200 for any path, or plain 404s). **Not added.** A provider here could be revisited if their own publishing changes. |
| NVIDIA AI | `blogs.nvidia.com/feed/`, and the `generative-ai` category feed | A **real, working** official RSS feed exists (`blogs.nvidia.com/blog/category/generative-ai/feed/`). **Deliberately not added**: NVIDIA's blog leans heavily toward enterprise/partner and hardware-sales content, a weaker match to the profile's "AI agents/LLM systems, mathematics" interests than the sources already covered, and it isn't a frontier-*model* lab in the same sense as the others. Flagged here as a viable future addition if the reader wants GPU/infra coverage. |

## Already covered indirectly

Hacker News (`min_points = 100`) and GitHub (`query = "llm OR agent OR ai"`, `min_stars = 50`) already
pick up frontier-lab news that gets enough independent attention — including, per the fixture used in
`docs/design.md`'s Jev notes, Anthropic and Groq mentions. This is real coverage, but it is *indirect*
and threshold-gated: a quiet announcement, or one that does not go viral before PIA's fetch window
closes, is missed. That gap is exactly what "Opus 5.5" fell into, and it is not closed by this change
for Anthropic, Groq or Z.ai, because none of them has a clean official mechanism to add.

## If this changes later

Revisit Anthropic, Groq and Z.ai/GLM if any of them starts publishing a real RSS/Atom feed, a public
changelog API, or consistently tags model releases on GitHub. Until then, adding them would mean
scraping rendered HTML, which this project has decided against (see the top-level rule above and
`docs/design.md`'s "Future consideration: article text" for the same reasoning applied to a related
problem).
