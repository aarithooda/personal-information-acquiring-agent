# PIA Stage 1 benchmark

A small, honest experiment: **how well does PIA's Stage 1 (Jev, or the older LLM triage) find the items its reader
actually wants?** The reader labels 186 real items blind; the tooling scores each Stage 1 against those labels.

Benchmark version: **v1**. Every report prints it. See rule 7 under *Decision rules* for what a new version means.

Nothing under `src/pia` is changed by this folder. It reads production code, never modifies it, and never touches
`data/pia.db` except to copy item text out of it read-only, once.

## Do this

```bash
.venv\Scripts\python.exe -m benchmarks snapshot     # once: freeze the 186 items (done already if benchmarks/data/items.json exists)
.venv\Scripts\python.exe -m benchmarks init         # once: create the shuffled labelling queue (done already if queue.json exists)
```

Then label, as many at a time as you like. **Double-click `benchmarks\label.bat`**, or:

```bash
.venv\Scripts\python.exe -m benchmarks label
```

One key per item: `y` SHOW · `m` MAYBE · `n` SKIP · `o` open the link · `b` back one · `q` save and quit. (`1` `2` `3` work too.)
Every label is written to disk the instant you press the key, so closing the window loses nothing; run it again to continue.
`python -m benchmarks status` shows progress. It takes 226 key presses (186 items, 40 of them shown a second time); about 30-45 minutes.

When everything is labelled:

```bash
.venv\Scripts\python.exe -m benchmarks freeze       # lock the labels: they become the yardstick
.venv\Scripts\python.exe -m benchmarks arm jev      # freeze the current Jev Stage 1 output (a few cents)   [skip if already in benchmarks/data/arms]
.venv\Scripts\python.exe -m benchmarks arm llm      # freeze the current LLM Stage 1 output (free tier, slow) [skip if already there]
.venv\Scripts\python.exe -m benchmarks analyze      # the report: printed, and saved under benchmarks/data/reports
```

`arm` prints counts only (never items or scores), so it is safe to run while you are still labelling. `analyze --allow-partial`
also works mid-way, but it is marked PRELIMINARY and **withholds every list of items**, so it cannot influence the labels you
have left to make.

## Why human labels are necessary

Stage 1 exists to answer "would this reader want to see this?". The only thing that can define the right answer is the
reader. Everything else PIA produces is a model's *opinion* of that answer. To ask "is Jev any good?" we need something
to compare Jev's opinion with that is **not** Jev's or another model's opinion. Human labels are the ground truth; the rest
of this folder is bookkeeping around them.

> **LEARNING NOTE: ground truth.** Evaluation needs a reference that is independent of the thing being evaluated. If the
> reference comes from the system (or a sibling system), an evaluation measures agreement with a model, not quality.

## Why the "shown" items are circular

It is tempting to say "the items PIA showed me are the good ones" and score Stage 1 by whether it ranks them high. Don't.

1. **Circular.** Those items were shown *because* a Stage 1 ranked them high. A system scored against its own past output
   looks excellent by construction. (And the briefings in your database came from the old LLM Stage 1, so scoring Jev against
   them would quietly reward Jev for resembling the LLM.)
2. **Selection bias.** An item that was never shown never got a chance to be judged wanted. "Shown" measures what was surfaced,
   not what you would have wanted, and the misses (the important failure) are invisible in it by definition.
3. **Exposure effects.** You tend to like what you were given, and you can only favourite what you saw.

So the labelling screen carries **no** score, rank, briefing status or popularity number, and the snapshot file does not even
contain them. The labeller never loads model output; a test checks the labelling module's imports to keep it that way.
You can't un-see the ~35 items from earlier briefings; judge each on its merits anyway, and see *Threats* below.

## How to label

Judge each item **as it appears on screen**: the same source label, title and (up to 500 characters of) text that Jev receives.
Ask: *if this appeared in my briefing, would I be glad?*

| Label | Meaning |
|---|---|
| **SHOW** | I would want this surfaced in my briefing. |
| **MAYBE** | Interesting enough that I might want it, but not clearly worth a headline. |
| **SKIP** | I would not want this surfaced. |

Tips: go with your first reaction; don't over-think MAYBE (it is a real answer, and the analysis treats it separately);
label what the item *is*, not whether you already knew about it; if the title alone is too vague, press `o` to read the
link (that is recorded, and it is fine, but decide whether the *title as shown* deserved a spot). About 40 items reappear later on
purpose. Just label them again as you feel; don't try to remember. Your consistency on those is a result, not a test.

## Design decisions (and what would change them)

| Decision | Why | Alternative |
|---|---|---|
| Items are **frozen** in `items.json`, identified by a content hash; every label and result records it | The database keeps changing; a benchmark needs a fixed set so today's and next year's numbers are comparable | Query the live database (labels would drift out from under results) |
| Queue is a **seeded shuffle** | Fatigue and drift fall evenly across sources; any labelled *prefix* is a random sample, so a half-finished session still gives an honest (noisier) preview | Database order (clusters by source/date) |
| **40 items repeated** far apart, unannounced | Measures your test-retest consistency, which caps how well *any* ranker can agree with you | Trust the labels blindly |
| Labels are an **append-only file**; latest wins | Crash-safe, resumable, history kept; a half-written last line is ignored, a corrupt middle line is an error | Overwrite a JSON file (a crash could lose everything) |
| An **arm is a file of raw Stage 1 output** and is scored by re-running PIA's own `state.enriched_items` + `rank_items` | The benchmark measures the ranking PIA really uses, and a change to `derive()` or `rank.py` can be re-scored on the same labels with no API calls (`rescore`) | Re-implement ranking here (would silently drift from production) |
| A **contract test** runs the real `make_curator` and requires the same shortlist as the benchmark reconstructs | The one-line "never show category other / importance 1" filter lives inside `curate.py`; if it changes, a test fails instead of the benchmark quietly measuring the wrong thing | Trust a comment |
| Results are **frozen once written**; `--force` to replace | A result you can overwrite is not a result | |
| Item text, labels and results live in `benchmarks/data/` and are **git-ignored** | They describe your reading habits and opinions | Commit them |

> **LEARNING NOTE: blinding and fixed order.** Two ways a labeller's judgment gets contaminated are seeing what the system
> thinks (anchoring) and seeing items in a patterned order (drift, contrast effects). The first is removed by not loading the
> data; the second by shuffling.

## Metrics: what each one means

A "ranking" is PIA's production order: filter, then model score + popularity percentile + cross-source boost. Every metric is
computed at four cut-offs, **K = 4, 10, 16, 32**, because the editor (Stage 2) only ever sees the top `2N+2` items:

| Days away | Headlines N | Editor sees top K |
|---|---|---|
| 1 | 1 | 4 |
| 3 | 4 | 10 |
| 5 (the first real run) | 7 | 16 |
| 10 | 15 | 32 |

Stage 2 can throw a bad candidate away, but it can never rescue a good item Stage 1 left out. So **recall@K is the limit on what
any briefing can contain**; precision@K mostly decides how much work the editor has.

| Metric | In words | Good is |
|---|---|---|
| **recall@K** | of all your SHOW items, the share in the top K | high |
| **precision@K** | of the top K, the share that are SHOW | high (but capped: if you have only 8 SHOW items, precision@16 cannot exceed 0.5) |
| **capture@K** | hits divided by the *most any ranking could get* at that K, `min(K, #SHOW)`. Precision and recall are each capped by the size of your SHOW set; capture is not | high (1.0 = perfect at this K) |
| **nDCG@K** | like precision, but SHOW counts double MAYBE and hits near the top count more (gains 2/1/0, log discount) | high (1.0 = perfect) |
| **AP** (average precision, = PR-AUC) | how early the SHOW items appear, averaged over all of them; punishes burying them | high |
| **AUC (show vs skip)** | chance a random SHOW item outranks a random SKIP item (MAYBE ignored). 0.5 = coin flip | high |
| **AUC (show vs rest)** | same, MAYBE counted as not-SHOW | high |
| **Spearman** | rank correlation between the system's order and your grades. Only three grade levels exist, so even a perfect ranking scores below 1 | high |
| `*_lenient` | the same, counting MAYBE as a hit | high |

All are reported for the **production ranking** and for the **model score alone** (no popularity, no cross-source boost), so you
can tell whether the model or the ranker on top of it is doing the work. Numbers come with 95% **bootstrap** intervals
(items are resampled 1000 times). Two arms are compared on the *same* resamples, so a difference is a paired difference
with its own interval and a "P(better)".

Also in the report: performance by **title-only vs has text**, **source**, **category** (Jev's) and **wildcard candidate**
(Jev's `wildcard` answer >= 0.5); the **missed SHOW items** with the reason (never shown / pushed down by popularity / low model score)
and Jev's per-question answers; **wrongly surfaced SKIP items**; the **top disagreements** between arms, with your label
beside each; and your **test-retest consistency**.

> **LEARNING NOTE: why several metrics.** No single number captures ranking quality. Recall@K asks "did we find it?",
> precision@K "how much junk came with it?", AP/AUC "how good is the whole order, regardless of where we cut?". They can
> disagree, and when they do, the disagreement is information.

## What 186 labelled items can and cannot support

Uncertainty is dominated by how many SHOW items you have. Approximate 95% half-widths (normal approximations; the report
computes the exact bootstrap):

| SHOW items | recall/precision at 0.5 | at 0.8 | AUC if truly 0.8 |
|---|---|---|---|
| 15 | ±0.25 | ±0.20 | ±0.14 |
| 25 | ±0.20 | ±0.16 | ±0.11 |
| 40 | ±0.15 | ±0.12 | ±0.09 |
| 60 | ±0.13 | ±0.10 | ±0.07 |

**Can support**
- A first, honest measurement of both baselines, and whether one is *clearly* better on the whole ranking (AP, AUC) if the gap is large.
- Spotting the *kind* of failure: are missed SHOW items title-only? from one source? wildcards? The false-negative list is
  the most useful part of the report.
- A frozen yardstick to compare later ranking changes against, without new API calls.
- How consistent you are (the noise ceiling).

**Cannot support**
- Small differences. With ~25-40 SHOW items, two arms have to differ by roughly 0.1-0.2 in recall@K before it stands out from noise.
  Whole-ranking metrics (AP, AUC, nDCG) are more sensitive than recall at a single K, and paired comparison helps when the arms make
  correlated errors.
- Anything about small groups. A source or category with fewer than 5 SHOW items is marked *(few)*: its recall moves in steps of
  20% or more.
- Calibration. `relevance` is a score to rank by, not a probability; no calibration claim is made.
- **Stage 2.** The editor's picks and explanations are not measured here; only what Stage 1 + ranking put in front of it.
- Anything about *other readers*, *other weeks* or *other sources*. One reader, one snapshot, five days of items.
- "The profile matters more than the model": that needs a third arm (LLM + the same profile), which does not exist yet.

## Threats to validity (read before believing a number)

- **One labeller who is also the profile's author.** Their labels and the profile share a mind, which favours the profile-based arm.
  That may be exactly what "works for me" means, but say so if you ever publish numbers.
- **Memory of earlier briefings.** ~35 items were shown to you before; you may label them differently.
- **Title-only items (61%) are judged on a title.** The labeller sees what the model sees, so a bad title is a fair miss for both.
  Opening a link (`o`) is recorded (`opened` in `labels.jsonl`) so its effect can be checked.
- **MAYBE is subjective.** Strict (SHOW only) and lenient (SHOW+MAYBE) results are both reported.
- **Sampling:** one snapshot of one window; the items were the first ~5 days after PIA was first run.
- **Non-determinism.** A model may answer differently on another day. Arms are frozen files, so a report is reproducible; a fresh
  `arm` run may not reproduce an old one exactly. That is a property worth measuring, not hiding.
- **Multiple comparisons.** The report shows many metrics and groups. If you go looking you will find something. Decide the
  primary metric *before* looking (below).

## Decision rules (benchmark v1)

These rules were fixed before any label existed. The git commit that introduced this wording predates every label
(`git log -p benchmarks/README.md` shows it). Rule 7 governs any later change.

**Label mapping (a definition, not a preference).** Each label has a fixed grade, and each metric uses it in one fixed way:

| Label | Grade | nDCG gain | AP |
|---|---|---|---|
| SHOW | 2 | 2 | relevant |
| MAYBE | 1 | 1 | not relevant |
| SKIP | 0 | 0 | not relevant |

nDCG uses SHOW = 2, MAYBE = 1, SKIP = 0 as gains. AP treats SHOW as relevant and MAYBE and SKIP as non-relevant. The
recall, precision and capture metrics also count SHOW only. The `*_lenient` variants (MAYBE counted as relevant) are
supplementary and are never used for a decision.

1. **Primary metrics:** capture@16 and nDCG@16 (the shortlist of a typical 5-day gap); AP as the whole-ranking check.
2. **Jev vs the LLM baseline:** report the paired difference in AP and nDCG@16 with its 95% interval. If the interval includes 0 the
   honest statement is "cannot tell", not "equal".
3. **Adopting a ranking change** (discovery lane, title-only fix, new weights): the paired nDCG@16 difference against the current
   Jev arm has a 95% interval above 0, **and** the title-only guardrail holds:
   `new title_only recall@16 >= current Jev title_only recall@16`.
   Here `title_only` is the report's "title-only" group (items with no non-whitespace text), `recall@16` is the share of that
   group's SHOW items inside the arm's production top 16, and "current Jev" is the frozen arm named `jev` in
   `benchmarks/data/arms` (its profile hash and commit are recorded in the report). Equal passes; anything lower fails.
4. **No tuning on the same labels you test on.** Every labelled item has a `fold` (0-4, from its URL hash). Fitting weights on all
   186 and reporting on the same 186 flatters the result. Tune on some folds and report on the rest, or collect fresh labels.
5. **Look at misses before changing anything.** The missed-item list says *why* an item was missed; fix the cause, not the number.
6. **What this benchmark is.** It is a development/evaluation benchmark: one reader, one snapshot of 186 items from about five
   days, so it can rank candidate changes and catch regressions on this reader's data. It is not definitive evidence of
   generalization to other readers, weeks or sources. The 5-fold rule in rule 4 only guards against overfitting these labels
   (the folds come from the same 186 items), so a claim beyond "better on this benchmark" needs fresh labels.
7. **No changes after seeing results.** Once `analyze` has been run on complete labels, the benchmark's methodology, labels and
   decision rules must not be changed after seeing results. That covers the label definitions and mapping, the snapshot, the
   queue, the frozen labels, the K values, the primary metrics, the guardrail, the group definitions, and these rules. Any
   substantive change creates a new benchmark version: bump `BENCHMARK_VERSION` in `benchmarks/common.py`, add an entry
   below, keep the old reports, and never compare numbers across versions. A tooling bug fix that alters any reported number
   is substantive. Not substantive: prose and typo fixes, speed-ups that leave every number unchanged, scoring a new arm, and
   extra analyses that are labelled exploratory and not used for a decision. (The same applies to relabelling:
   `freeze --force` after results have been seen starts a new version.)

### Version history

- **v1** (2026-09-21): snapshot `1c20c65fe2ca` (186 items), queue seed 20260921 (226 screens, 40 repeats, minimum gap 30),
  labels SHOW 2 / MAYBE 1 / SKIP 0, K = 4, 10, 16, 32, baseline arms `jev` and `llm`.

## Comparing a future change against the same labels

```bash
# 1. edit src/pia/jev/triage.py (derive: the weights) or src/pia/briefing/rank.py
.venv\Scripts\python.exe -m benchmarks rescore jev --name jev-v1      # apply the CURRENT formula to jev's stored raw answers; no API calls
.venv\Scripts\python.exe -m benchmarks analyze --arms jev,jev-v1      # jev is the baseline; the report gives the paired difference
```

`rescore` re-derives relevance from Jev's stored raw answers; the ranking is re-run by production code. A change to *which
questions Jev is asked* (or the profile) needs a fresh `arm jev --name jev-v2`, which costs a few cents. Every report prints
the snapshot id and the labels hash; two reports are comparable only if both match.

## Files

```
benchmarks/
  README.md            this file
  label.bat            double-click to label
  cli.py __main__.py   python -m benchmarks <command>
  snapshot.py          freeze the items (facts only: no model output, no briefing status)
  labelling.py         queue, append-only label store, the terminal screen (imports no model data)
  labelset.py          freeze labels, folds, test-retest
  arms.py              run production Stage 1 on the snapshot; rescore; PIA's own ranking over stored output
  metrics.py           pure metric functions and the bootstrap
  analysis.py          strata, misses, disagreements, the Markdown/JSON report
  data/                git-ignored: items.json, queue.json, labels.jsonl, labels_frozen.json, arms/, scratch/, reports/
tests/test_bench_*.py  the tests (they use synthetic data; none touches your real database or the network)
```

`labels.jsonl` carries `item_id` (the id in `data/pia.db`); `labels_frozen.json` carries `item_id` and the canonical URL, so labels can be
joined with anything else. Never edit `items.json`: it is hash-checked and the tooling will refuse a changed file.
