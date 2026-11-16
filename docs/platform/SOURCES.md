# Sources: coverage, reliability and failure behaviour

## Rule for a source

Preference order, strongest first: an official API, an official RSS or Atom feed, a public JSON endpoint with a
stable shape, a page watched for new links, mail. Rendered-HTML scraping of article bodies is not used for
acquisition. A page is fetched only on demand (the reader, opportunity details) through the guarded fetcher.

## The catalogue

`config/catalog.toml` holds 142 sources. Every RSS entry was fetched on 2026-11-15 and kept only if it returned
HTTP 200, was a real feed, had at least three entries and its newest entry was within 90 days. 77 start active;
65 start as suggestions (high-volume general news, aggregators, opportunity listings, niche blogs).

| Space | Active | Suggested | Notes |
|---|---|---|---|
| AI & ML | 15 | 4 | Lab blogs (OpenAI, DeepMind, Google, Microsoft, Apple, Mistral, Hugging Face), HF Daily Papers, arXiv sample, GitHub |
| Software | 13 | 4 | Hacker News (150+ points), engineering blogs, language blogs, LWN |
| Mathematics | 3 | 5 | Quanta, Terence Tao, arXiv sample |
| Physics | 5 | 7 | Physics World, APS Physics, CERN Courier, arXiv sample |
| Robotics | 4 | 2 | IEEE Spectrum, The Robot Report, Robohub, arXiv cs.RO |
| Quant & markets | 5 | 5 | Jane Street, Quantocracy, Alpha Architect, Fed releases, arXiv q-fin |
| Economics | 5 | 8 | VoxEU, Marginal Revolution, Project Syndicate, NY Fed, arXiv econ |
| Geopolitics | 6 | 3 | Foreign Affairs, Foreign Policy, War on the Rocks, The Diplomat, Crisis Group, RAND |
| Startups | 2 | 5 | Y Combinator, Stratechery; TechCrunch and others suggested (volume) |
| Science & tech | 4 | 7 | Science, MIT Technology Review, The Conversation, IEEE Spectrum |
| Research opportunities | 1 | 5 | Google Open Source blog; aggregators suggested only |
| Hackathons & competitions | 1 | 2 | Codeforces API; Devpost API suggested only |
| University | 0 | 0 | No verified source yet (see gaps) |
| Security & systems | 8 | 3 | Schneier, Krebs, Project Zero, Trail of Bits, Talos, SANS ISC, arXiv cs.CR |
| Space | 5 | 5 | NASA, SpaceNews, ESO, Planetary Society, arXiv astro-ph |

## Known gaps (checked, not assumed)

- **Anthropic, Meta AI**: no official feed (`/rss.xml`, `/news/rss.xml` and others return 404). They reach PIA
  only through Hacker News. Mistral does have one and is included.
- **ISRO, MIT News, NBER, IMF, CISA, CFR, World Bank, CERN news**: no working feed, or the feed refuses
  automated clients (403).
- **Kaggle, Unstop, Devfolio, MLH events**: no public feed or API found.
- **arXiv is sampled**: the newest N per category group, twice a day. arXiv has no popularity signal; Hugging
  Face Daily Papers covers the community-selected ML papers.
- **GitHub** finds repositories created recently that already have stars; an old repository that suddenly
  becomes popular is missed.
- **University**: the institute's website timed out from the build machine, so nothing could be verified.

## How a source behaves when it fails

- A failure is recorded against that source only; the cycle continues with the rest.
- The source is retried with exponential backoff (interval doubling, capped at 24 hours).
- After five consecutive failures the Sources page shows it as failing repeatedly. It is never removed
  automatically; the owner pauses or removes it.
- Its cursor does not advance on failure, so when it recovers it catches up (up to 30 days back).
- A source that was down the very first time is anchored to that first attempt, so it does not lose what it
  published in the meantime.
- Items are stored before the cursor advances; a crash between the two only causes a harmless re-fetch.

## Discovery and evaluation

Discovery never follows anything. It creates a `candidate` with an evaluation and a reason:

- from the catalogue (the 65 optional entries);
- from sites that well-ranked items keep linking to, if a feed can be found there;
- from a model's suggestions for the owner's top interests, but only if PIA can fetch a real feed at the address.

Each candidate is measured: days since the latest entry, posts per week, whether entries carry text, how many
recent entries match the owner's interests, and how much of it already arrives through other sources. A model's
view of provenance and noise is shown beside the measurements, labelled as an opinion.

## Opportunity monitoring

For an organisation the owner adds, PIA takes a page address and works out the best mechanism:

1. a job board it knows (Greenhouse, Lever, Ashby): the board's public JSON API, filtered to early-career titles;
2. a feed, if the page advertises one;
3. otherwise the page itself, watched for new links whose text looks like an opportunity.

A page rendered only by JavaScript has no links to see; the source then reports that plainly instead of
appearing healthy and empty.
