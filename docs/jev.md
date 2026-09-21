# Jev in PIA

How PIA uses a decision model for its first-pass triage, why, and what is and is not known about how well it works.

> **Read this first.** PIA's own evaluation ([evaluation.md](evaluation.md)) shows that the current Jev design ranks the
> reader's wanted items **above chance**. It does **not** show that Jev is better than the LLM triage it can replace, or
> better than the earlier Jev design. Jev is used here for an architectural reason (cheap, structured, inspectable judgment
> before expensive generation), not because it was shown to be more accurate than a larger model.

## What Jev is

Jev is a hosted "decision model" from TypeSafe, a third-party vendor. It is not a chat model and cannot write text.
You give it a piece of **state** (here: an item, and parts of the reader's interest profile) and a set of **typed
questions**, and it returns typed answers:

| Primitive | Question | Answer PIA uses |
|---|---|---|
| **Noul** | a yes/no proposition | `P(yes)` in [0, 1] |
| **Choice** | pick one of N labelled options | the probability of *every* option (not just the winner) |
| **Score** | an ordered scale | not used by the current design (see below) |

Each answer also carries a confidence. PIA calls the HTTP API directly with `httpx` (`src/pia/jev/client.py`) and validates
every response into typed models, so downstream code only ever sees values of a known shape. The request/response format
follows TypeSafe's public API documentation. PIA needs your own TypeSafe API key; Jev is optional (see "Failure handling").

## What Jev does in PIA, and what it does not

Jev is **Stage 1**: for every *new* item it answers a fixed set of small questions about how the item relates to the
reader's own interest profile. Deterministic code turns the answers into a relevance number between 0 and 1, which feeds the
ranking. That is all.

- Jev **does not summarise or write anything.** It cannot. Items that Jev triages carry no generated summary; a
  one-line extra in the briefing is a bare link.
- Jev **does not choose the briefing.** Deterministic ranking builds a shortlist, and the stronger LLM editor (Groq,
  `openai/gpt-oss-120b`) picks and explains the headlines from it.
- Jev **does not see the whole profile**, and does not see popularity numbers.

## Why Jev, and why the stronger LLM afterwards

Two different jobs sit in this pipeline, and they want different tools:

| Job | Volume | Needs | Used |
|---|---|---|---|
| Decide, for every new item, how well it fits the reader's stated interests | every item (about 150 to 200 per check) | cheap, fast, structured, inspectable, decomposable | Jev |
| Choose a few headlines from a shortlist and explain them in prose | about 15 to 35 items | comparison across items, grounded writing, judgment about what matters together | Groq `gpt-oss-120b` |

Jev answers narrow, typed questions with probabilities the code can combine and audit; a generative model is the right
tool for the editorial step. This is a division of labour, not a claim about accuracy. PIA also keeps an LLM triage (Groq
`gpt-oss-20b`) as the alternative and as the automatic fallback, so the two can be compared, and were (see the evaluation).

## The typed-question approach

The current design is `jev-triage-v2` (`src/pia/jev/design.py`). Every rule comes from TypeSafe's documentation, not from
fitting against labelled data:

1. **One condition per Noul.** Each *entry* of the reader's own lists (each valued signal, each low-value pattern, each
   exception, each wildcard, each thing they are building) becomes its own yes/no question, pointing at the entry with a
   path such as `` `reader_profile.discovery.strong_wildcards[2]` ``. A question that bundles two conditions returns a
   value that "means less", so the code combines them instead.
2. **Priority is one Choice.** "Which priority tier contains the area that the item is mainly about?", with the reader's own
   tiers plus `none` as the options. The code uses the whole probability distribution: the expected rank weight, where the
   weights are `(n - rank) / n` (the profile orders tiers but gives no magnitudes, so linear-by-rank invents none).
3. **No Score question feeds the formula.** A Score's decimal has weak numerical calibration; probabilities are the
   calibrated part.
4. **Only the state a question needs.** Accuracy falls when the state carries content unrelated to the decision, so each
   item is sent as **four requests**, each with only the profile slice its questions point at. The item-only request
   (category, "is this an injection attempt", "is the subject unclear") sends no profile at all.
5. **Confidence describes whether to trust an answer, not what it is.** It is stored beside the score, never multiplied into it.
6. **Arithmetic in code.** Jev is not a calculator.

## Profile-driven personalisation

The taste comes from the reader's `config/interests.toml` (template: `config/interests.example.toml`), compiled by
`src/pia/profile.py` into two renderings from one source of truth: a trimmed JSON state for Jev, and readable text for the
editor. The profile file is git-ignored because it describes a person. Not sent to Jev: your name, character descriptions,
current projects, learning style and the free-text guidance sections. The profile's content hash is stored with every result.

## How raw Jev outputs are stored

Each triaged item stores, in `enrichments.details` (JSON): the **raw answer to every question**, the plan (what the question
ids mean), a few facts (for example whether the item was title-only), the evidence (which question drove reach, value and
penalty), per-request token usage, and every model id that answered. It also stores the question-set version and the
profile hash. Because the raw answers are kept, the formula can change and old rows can be re-derived **offline**, with no
new API calls (`design_for(question_set)` maps a stored row to the code that derives it). Rows written by the earlier
design, `jev-triage-v1`, are still readable.

## How deterministic code derives relevance

```
reach     = max( expected priority-tier weight,  best "building" question,  best wildcard question )
low_value = strongest matching low-value pattern x (1 - strongest matching exception)
value     = best valued-signal question (each list weighted by rank)
relevance = reach x (1 - low_value) x (0.5 + 0.5 x value)
```

- Any one route to relevance suffices, mirroring the profile's own rule. Substance alone earns nothing.
- `max` rather than a sum, because the probabilities are correlated and the documentation guarantees no structural
  invariants, so thirteen weak 0.2s must not add up to a penalty.
- `value` can at most **halve** relevance, so evidence that a title cannot carry never erases a strong fit. That `0.5`
  (`VALUE_FLOOR`) is the only constant in the formula: a named prior with a stated reason, **not a fitted number**.
- A near-certain injection (probability of at least 0.90) zeroes the relevance and is flagged.
- Ranking then uses `1 + 4 x relevance` as the model's judgment, plus a per-source popularity percentile and a cross-source
  boost (`src/pia/briefing/rank.py`). Popularity is deliberately kept out of the Jev decision layer.

## Model and version configuration

- `JEV_MODEL` (in `.env` or the environment) pins a version, for example `JEV_MODEL=jev-1.13.0`. The default is the alias
  `jev-latest`, which moves with vendor releases; TypeSafe recommends pinning when results are compared over time. Pinning is
  opt-in because a pinned version can eventually be retired.
- Whichever way it is requested, every stored row records the versioned model id that actually answered.
- `--triage auto|jev|llm` (or `PIA_TRIAGE`) chooses the Stage 1. `auto` uses Jev when a key **and** a profile exist. `jev` is
  strict and fails loudly if either is missing. An invalid profile is always an error, never silently ignored.

## Failure handling

Jev is treated as an unreliable dependency, with the same discipline as the LLM stage:

- Requests go through PIA's shared retrying HTTP layer. Errors reuse the LLM taxonomy: HTTP 400/422 or an unusable answer is
  a *bad output* (possibly this item's fault); everything else (network, auth, 429/5xx after retries) is *unavailable*
  (never held against the item).
- An item is triaged with bounded concurrency (4 in flight) and results are saved as they arrive; a circuit breaker stops
  submitting work after repeated failures instead of burning the backlog against an outage.
- An item that fails triage three times becomes `failed` and is excluded from briefings; `pia status` counts them.
- If Jev stops responding, the LLM triage finishes the remaining items (`auto` says so). One failing request out of an
  item's four fails that item: there is no partial credit.
- When a run cannot triage anything, nothing is committed and the checkpoint does not move.

## Cost considerations

Measured on real runs (indicative only; prices and behaviour are the vendor's and can change): about **6.5k to 7k input
tokens and about 1.6 s per item**, four requests per item, roughly **$0.0003 per item**. Benchmark V2 scored its 200 items with
Jev V2 using about 1.36 million input tokens in total. Most of the tokens are the profile slices, which is why the
example profile advises keeping the profile short, and why `pia doctor` reports a per-request token estimate.

## Known limitations

- **Not shown to beat the LLM triage or Jev V1.** On a fresh, blind corpus the three were statistically indistinguishable
  ([evaluation.md](evaluation.md)). Jev V2 was nominally lowest of the three on two of the three primary metrics, with wide
  intervals that include zero.
- **Title-only items are under-represented in the top of the ranking.** Most Hacker News items have no text. In the
  evaluation, no title-only item reached Jev V2's top 16. The design records that an item had no text but does not
  compensate for it; a structural fix (a reserved lane for uncertain-but-promising items) is not built.
- **Top-K shortlists can be homogeneous.** The profile asks for discovery, but no discovery lane exists, so a run can be
  almost all AI items.
- **The weights are one named prior, unvalidated beyond the evaluation above**, and per-entry Noul calibration is unmeasured.
- **One reader, one profile.** Everything measured is about a single person's taste.
- **A vendor dependency.** The model, its versions and its pricing are outside this repository.

## The evolution from V1 to V2

`jev-triage-v1` used nine questions per item, sent the whole profile with every question, and combined answers with an
additive blend. Reading TypeSafe's documentation against that design showed that it:

- asked **compound questions** (one Noul about a whole list, a 13-way disjunction);
- sent **excessive unrelated context** (about 4k tokens of profile with every question, even ones that need none);
- used **vague conditions** ("could *plausibly* become a component of...", read literally by the model);
- treated a **Score's decimal as a precise scale** and ignored its probabilities;
- stored confidence and a `too_little_info` signal and **never used them**.

`jev-triage-v2` redesigned the question set around the primitives (atomic questions, one Choice for priority, per-request
profile slices, a non-compensatory formula with one named prior). It was designed **from the documentation and first
principles, not by tuning against labels**, and it was then evaluated on a new corpus. That evaluation did not establish that
V2 is better than V1. Both designs remain in the code; the full reasoning, decisions and rejected alternatives are in
[design.md](design.md) ("Jev at Stage 1" and "Jev layer v2").
