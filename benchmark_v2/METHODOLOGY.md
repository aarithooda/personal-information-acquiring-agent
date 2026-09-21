# Benchmark v2 methodology (FROZEN before any label exists)

**Benchmark version: `v2`.** This document is the contract. It was written before the corpus was collected, and it is
finalised and committed before the reader sees a single item. It must not be changed after labels or results exist (section 15).
Every number in it that the code uses is a named constant in `benchmark_v2/common.py`, and a test checks the two agree.

## 1. What this benchmark is for

To measure, on a **fresh, independent** corpus, how well the current production Jev layer (`jev-triage-v2`, question set
`jev-triage-v2`, unchanged since commit `7479fdc`) ranks the items its one reader actually wants, and how that compares with the
older LLM Stage 1 and with simple reference orderings. It is a **development/evaluation benchmark for one reader**: it can rank
candidate designs and expose failure modes on this reader's data. It is not evidence that anything generalises to other readers,
periods or sources.

## 2. Independence and an honest declaration of what the author knew

Benchmark v1 (186 items, frozen) and its analysis exist and were seen by the person who wrote this methodology. Therefore:

* **Corpus construction and labelling use no v1 label, v1 score, v1 rank, v1 model output or v1 analysis.** The v2 code never opens
  the v1 labels file, arms, reports or `labelset`; a test checks its imports. The only v1 artefact it reads is the facts-only item
  snapshot `benchmarks/data/items.json` (titles, text, URLs, identities), used for (a) the absence check and (b) drawing the repeats.
* **Two design choices below were informed by v1 in a general, statistical way, not by any v1 item:** (i) the *primary metrics are
  graded/lenient* because v1 showed that strict SHOW labels are too rare for reliable inference at this corpus size; (ii) *simple,
  model-free reference orderings and a title-only analysis are pre-declared* because v1 showed those comparisons matter. Neither
  choice depends on which items are in this corpus.
* The systems under test (Jev v2 code, profile, ranking) are frozen and are not adjusted on this corpus (section 15).

## 3. Corpus construction (fixed procedure)

**Corpus = 200 items = 160 new + 40 repeats.** The 160 new items are the main independent evaluation set. The 40 repeats are
reliability checks and are **never** counted as independent test items.

### 3.1 The 160 new items

1. **Population.** Everything PIA's own source mechanisms would have returned for the historical period
   **2026-07-19T00:00Z (inclusive) to 2026-09-17T00:00Z (exclusive), 60 days**, fetched in **20 consecutive 3-day windows**
   (3 days = PIA's `FIRST_RUN_LOOKBACK`), in chronological order, sources in `config/sources.toml` order, with that file's
   filters and caps unchanged: arXiv (`cs.AI`, `cs.MA`, `cs.SE`, cap 100 per window), Hugging Face Daily Papers (>= 10 upvotes),
   Hacker News (>= 100 points, cap 100 per window), GitHub (`llm OR agent OR ai`, >= 50 stars, cap 30 per window), and the four RSS feeds.
   The window ends *before* Benchmark v1's intake began, so the two corpora do not overlap in time.
2. **Same machinery.** arXiv, Hacker News and RSS use the production adapters unchanged (they accept a window). The GitHub and
   Hugging Face production adapters cannot fetch a past window (GitHub sends no upper date bound; HF always returns the latest
   day), so the benchmark uses two thin **historical variants** that reuse the production parser, query, star/upvote filters and
   result caps and change only the request's date parameters. Items are stored with PIA's own `store_items`, so canonical-URL
   identity, cross-source merging of `signals`, and the `RawItem` representation are exactly production's.
3. **Completeness rule.** Every (source, window) fetch must succeed after PIA's retries. If any fails, the build stops and is rerun;
   it is never completed by dropping the failed part.
4. **Exclusion rules (mechanical, applied to identity only, never to content).** An item is excluded if and only if:
   **E1** its canonical URL equals the canonical URL of any Benchmark v1 corpus item, or of any item in `data/pia.db`; or
   **E2** its `(source, external_id)` equals that of any v1 corpus item; or **E3** PIA's `store_items` rejects it (blank title,
   unusable URL). Nothing else excludes an item: not its topic, text, length, popularity, or anything about how interesting it looks.
5. **Sampling.** From the eligible pool, sorted by canonical URL, draw **160 items uniformly at random without replacement** with
   `random.Random(20260922)` (`SAMPLE_SEED`). Selection uses only the sorted identity list. No human and no model looks at
   the items before they are drawn, and nothing is stratified, balanced, filtered or replaced, so the source, topic and
   title-only mix is the natural mix of the pool.
6. **Absence check.** After sampling, the code asserts that no new item's canonical URL or `(source, external_id)` occurs in the v1 corpus.

### 3.2 The 40 repeats

**40 items drawn uniformly at random without replacement** from the 186 v1 item ids with `random.Random(20260923)` (`REPEAT_SEED`),
using only the ids in the facts-only v1 snapshot. Their labels are not read. Each repeat is included exactly as frozen in v1 (same
title, text, URL, source, signals) and keeps its v1 identity in the internal manifest.

### 3.3 Identity and order

Every corpus item gets a v2 id `1..200` by sorting on `sha256("benchmark-v2:" + canonical_url)`, which carries no information about
origin. The labelling order is a seeded shuffle (`QUEUE_SEED = 20260924`) of all 200 with **no within-session repeats**, so an item
is shown once, and the 40 repeats are indistinguishable from new items in id, order and appearance. Origin, repeat identity
and v1 identity exist only in the internal manifest.

### 3.4 Known limitations of the corpus (declared now)

* **Hindsight in popularity signals.** Points, stars and upvotes are read at collection time, not at the item's original time, so
  older items show more accumulated popularity than PIA would have seen, and an item that later became popular can pass a filter it
  would have failed then. Popularity is hidden from the reader and is used only by the production ranker.
* **RSS depth.** Feeds only hold recent entries, so the RSS share reflects what feeds retain, not the full 60 days.
* **Caps apply per window** exactly as in production, so a busy window is a sample of its newest items.
* **Undated items** that an adapter returns are kept (production keeps them).
* **Memory.** The reader labelled v1 recently and may recognise some repeats.

## 4. Labelling protocol

**Labels.** `SHOW`: I would want this surfaced in my briefing. `MAYBE`: interesting enough that I might want it, but not clearly
worth a headline. `SKIP`: I would not want this surfaced. **Grades: SHOW = 2, MAYBE = 1, SKIP = 0.**

**What the reader sees, per item:** the source label, title and up to 500 characters of text, exactly what Jev is sent
(`snapshot.human_view`), and nothing else: no score, rank, popularity, cross-source signal, URL, date, id, prior label or any model output.
The reader may open the link (recorded). The item order is the shuffled queue; the reader is not told which items are repeats and
must not try to find out. The reader must not open the benchmark data files or run any analysis until labels are frozen. All 200
items are labelled; the session may be paused and resumed; a label may be changed by going back (the latest label counts, history is kept).

## 5. Order of operations (gates)

1. Methodology final and committed; corpus built and verified; `FREEZE.json` written (corpus id, manifest hash, methodology hash,
   production-code tree hash, profile hash, sources-config hash).  **No Jev output exists for this corpus.**
2. The reader labels all 200 items. Labels are frozen (`freeze-labels`).
3. Only then: run the arms (section 6) once each on all 200 items; freeze their raw outputs.
4. Run the analysis (sections 7 to 14) exactly as written; publish every pre-declared analysis whatever the outcome.
5. Any deviation is recorded in the report; a substantive one makes the result a new benchmark version (section 15).

## 6. Systems under test

* **A. `jev-v2` (primary):** the production Jev layer at the frozen code, profile and configuration. Model `jev-1.13.0` (pinned
  with `JEV_MODEL`); if unavailable, `jev-latest` with the resolved version recorded.
* **B. `llm-stage1`:** the production LLM Stage 1 (`gpt-oss-20b`, prompt `stage1-v2`, batches of 10, no profile), run once.
* **C. `jev-v1` (optional, descriptive):** the legacy question set through the legacy design.
* **Reference orderings (no model, no API):** **R1** newest first; **R2** items with text first, then newest first;
  **R3** popularity percentile only (production's within-source percentile, ties by newest).
* **Provenance recorded for every arm:** benchmark version, question-set version, Jev model id(s), profile hash, formula/derivation
  version, ranking version (the production `rank_items` code hash), code commit and production-tree hash, run time, tokens, failures.
* Each arm is run **once** on the frozen corpus. A run may be repeated only to complete items that failed to score, with identical code,
  configuration and inputs. No arm is tuned, re-prompted or re-parameterised on this corpus.

## 7. Ranking

Each arm's stored output is ranked by **PIA's production ranking** (`enriched_items`, the curator's filter, `rank_items`); items the
curator would never show are placed below all others by model score, and unscored items last, giving a total order. A "model-only"
order (model score alone, ties by newest) is reported as secondary. **The primary ranking pool is the 160 new items only**, ranked
among themselves, so popularity percentiles and top-K do not depend on the repeats. A secondary analysis ranks all 200.

## 8. Metrics, K values and the treatment of MAYBE

**K = 4, 10, 16, 32** (the editor's shortlist for 1, 3, 5 and 10 days away).

**Primary metrics (all on the 160 new items):**
* **P1 `nDCG@16`**, graded: `DCG = sum_{r=1..16} g_r / log2(r+1)` divided by the DCG of the ideal order of the same items (SHOW = 2, MAYBE = 1, SKIP = 0 as gains).
* **P2 `AP_lenient`**: average precision with SHOW and MAYBE both relevant.
* **P3 `AUC_lenient`**: probability that a random SHOW-or-MAYBE item is ranked above a random SKIP item.

**Secondary (reported, never used for a decision):** `nDCG@K` for every K; lenient `precision@K`, `recall@K`, `capture@K = hits / min(K, #positives)`;
strict (SHOW only) `recall@K`, `precision@K`, `AP`, and SHOW-vs-SKIP AUC; Spearman; the model-only variants.
**MAYBE is a first-class label:** it is graded 1 in P1 and counts as relevant in P2 and P3. Strict metrics are reported for completeness and,
if the corpus has fewer than 20 SHOW items, without inference (descriptive only).

## 9. Uncertainty

* **Confidence intervals:** percentile bootstrap, items resampled with replacement, **5,000 resamples** (`BOOTSTRAP_RESAMPLES`), seed 20260925, 95% interval,
  on the same resamples for every arm so differences are **paired**. (Items from one source/day are treated as independent: a stated simplification.)
* **Above-chance test:** one-sided permutation test, labels shuffled among items (counts fixed), **10,000 shuffles** (`PERMUTATION_SHUFFLES`), seed 20260926.

## 10. Repeat-label reliability (the 40 repeats)

For the 40 repeats, compare the v2 label with the v1 label: exact agreement, linearly weighted kappa, the 3 x 3 confusion matrix,
SHOW-to-SKIP flips, and the change in the SHOW+MAYBE rate. Also report a **noise reference**: the AUC_lenient obtained when the v1 label is
used as a ranker of the v2 label. These 40 are a reliability sample over a short interval with possible memory; they are **not**
independent test items and are excluded from P1 to P3. If weighted kappa is below **0.40** (`KAPPA_FLOOR`), every verdict in section 14 is
annotated "limited by label noise".

## 11. Title-only analysis (pre-declared)

For each of the primary metrics that can be computed within a stratum, and for the descriptive quantities below, split the 160 into
**title-only** (no non-whitespace text) and **has text**: (a) positive (SHOW+MAYBE) rate in each; (b) for each arm the share of the top 16 and top 32
that is title-only, against the title-only share of the corpus and of the positives; (c) **representation ratio** `RR = title-only share of the arm's top 32 / title-only share of all
positives`; (d) within-stratum AUC_lenient. **Flag (descriptive, not a gate):** an arm shows "title-only under-representation" if `RR < 0.5`.

## 12. Stratification

By source (natural sources), by **source-native category** (arXiv primary category and GitHub language where present; RSS feed name), by
the arm's own model category, and by title-only. Strata with fewer than 5 positives are marked and support no inference.

## 13. Comparisons

Paired bootstrap difference (arm minus comparator, same resamples) for P1, P2 and P3, with the 95% interval and the share of resamples
in which the arm is ahead. **Decision comparators: B (`llm-stage1`) and R2 (text first, then newest).** C, R1 and R3 are descriptive.

## 14. Decision rules (fixed now)

Let A be `jev-v2`. Verdicts use only the 160-item primary set.

* **D1, above chance.** "Ranking skill demonstrated" if **both** the permutation p-value of `AP_lenient` and of `AUC_lenient` are below **0.025**
  (Bonferroni over the two). Otherwise "not demonstrated".
* **D2, versus a decision comparator X.** "A outperforms X" only if the paired 95% interval of `AP_lenient(A) - AP_lenient(X)` is entirely above 0 **and**
  the point difference in `nDCG@16` is above 0. "A underperforms X" symmetrically. Otherwise **"cannot tell"**, which is not "equal".
* **D3, label noise.** If weighted kappa < 0.40, prefix every D1 and D2 verdict with "limited by label noise".
* **D4, prevalence.** If the number of SHOW+MAYBE among the 160 is below 20 or above 140, lenient metrics are declared "underpowered" and no D1 or D2 verdict is issued.
* No other quantity decides anything. Everything else is descriptive.

## 15. Rules against post-hoc tuning, and versioning

1. **The systems are frozen.** Between the freeze and the final report, nothing in `src/pia/`, `config/interests.toml`, `config/sources.toml`, the
   question set, the derivation formula, the ranking weights or the model configuration may change. `verify` recomputes the production-tree hash and
   the profile and config hashes and must match `FREEZE.json`. An evaluation run whose hashes differ is void.
2. **This corpus and its labels are used exactly once** to evaluate the frozen system. If the system is changed for any reason, the changed
   system is evaluated on a **new** corpus (a later benchmark version); this corpus is then spent for it.
3. **This methodology, the corpus, the labels and the decision rules must not be changed after seeing results.** Any substantive change
   (corpus, labels, label definitions, metrics, K values, interval method, decision rules, the arms, the analysis) creates a **new benchmark version**; results
   of different versions are never compared. Not substantive: prose and typo fixes, speed-ups that change no number.
4. **No labels are inspected before all 200 are labelled and frozen**, and no model output exists for this corpus before that.
5. **All pre-declared analyses are reported**, including those that come out unfavourably.
6. Exploratory analyses are allowed only if labelled exploratory in the report and they never feed a verdict or a system change.

## 16. What this benchmark cannot show

One reader (who is also the profile's author), one 60-day sample, popularity signals with hindsight, a single model version, a single run, ordinary
statistical uncertainty at n = 160 (about a 0.1 half-width on an AUC near 0.7), and no measurement of Stage 2 (the editor's picks and prose).

### Version history

* **v2** (2026-09-21): first version. Seeds 20260922 / 20260923 / 20260924 / 20260925 / 20260926 for sampling, repeats, queue, bootstrap, permutation.
