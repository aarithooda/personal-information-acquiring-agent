# Evaluation

What PIA's Stage 1 evaluation did, what it found, and what it does not support.

> **Summary.** Jev V2 ranked one reader's wanted items **above chance**. The evaluation could **not** tell Jev V2 apart from
> the LLM Stage 1 or from the earlier Jev V1 design. These are engineering measurements of one person's data, not a claim
> about model quality in general.

## What was measured, and what was not

Both benchmarks measure **Stage 1**: given a set of new items, how well does PIA's production ranking put the items its one
reader wanted near the top? They do not measure Stage 2 (the editor's choices and prose), calibration of the relevance number,
other readers, other periods, or other sources.

The "ground truth" is the reader's own labels, made blind: **SHOW** ("I would want this surfaced"), **MAYBE**, **SKIP**. These
labels are subjective and noisy. They are the yardstick for an engineering decision, not objective truth about what matters.
Notably, the reader is also the author of the interest profile that Jev is given.

## Timeline

1. **Benchmark V1** (186 items, blind labels: 5 SHOW / 39 MAYBE / 142 SKIP) compared the then-current Jev design (V1) with the
   LLM Stage 1. It **could not distinguish them**: on the production ranking, nDCG@16 was 0.29 (95% interval 0.09 to 0.48)
   for Jev V1 and 0.24 (0.06 to 0.49) for the LLM, and SHOW-versus-SKIP AUC was 0.65 and 0.66 with intervals that overlap
   almost completely. With only 5 SHOW items, strict metrics are too rare to support inference. V1 also suggested that a trivial
   model-free ordering can be competitive on a corpus this small, which is why V2 pre-declares such comparators.
2. **Jev V2 was designed afterwards**, from the vendor's documentation and first principles, **without tuning against V1's
   labels**. Because V1's labels had been seen (and analysed) by the designer, they were not used to evaluate the redesign.
   See [jev.md](jev.md) for what changed and why.
3. **Benchmark V2** used a **fresh corpus**: 160 new items drawn uniformly at random (seeded) from a 60-day historical replay of
   PIA's own sources, plus 40 random repeats of V1 items used only to measure the reader's label consistency. The
   methodology ([../benchmark_v2/METHODOLOGY.md](../benchmark_v2/METHODOLOGY.md)) was written and frozen **before any label or
   model output existed**, and `FREEZE.json` bound the corpus, the methodology and the production code's git tree hash so
   that "nothing changed afterwards" is checkable. Each system was run **once**.

## Benchmark V2 results

Primary metrics, on the 160 new items, each system ranked by PIA's production ranking (95% percentile bootstrap intervals,
5,000 resamples). "Lenient" counts SHOW and MAYBE (33 items, 20.6%) as relevant.

| system | nDCG@16 (graded) | AP (lenient) | AUC (lenient) |
|---|---|---|---|
| **Jev V2** (production) | 0.362 [0.150, 0.559] | 0.375 [0.229, 0.532] | 0.627 [0.516, 0.736] |
| LLM Stage 1 (`gpt-oss-20b`) | 0.316 [0.121, 0.523] | 0.387 [0.242, 0.549] | 0.670 [0.555, 0.774] |
| Jev V1 | 0.392 [0.169, 0.580] | 0.396 [0.245, 0.555] | 0.650 [0.541, 0.757] |
| Reference: newest first | 0.142 | 0.203 | 0.417 |
| Reference: items with text first | 0.233 | 0.244 | 0.503 |
| Reference: popularity only | 0.335 | 0.315 | 0.561 |

Pre-declared decisions (METHODOLOGY section 14):

| Question | Verdict | Basis |
|---|---|---|
| **D1: is Jev V2 above chance?** | **Ranking skill demonstrated** | one-sided permutation tests: AP p = 0.0035, AUC p = 0.0131 (both must be below 0.025) |
| D2: Jev V2 versus LLM Stage 1 | **Cannot tell** | paired differences: nDCG@16 +0.043, AP -0.014, AUC -0.043; every interval includes 0 |
| D2: Jev V2 versus "text first" | **Cannot tell** | AP +0.131 [-0.028, 0.290], AUC +0.126 [-0.011, 0.251]; intervals include 0 |
| D3: label noise | not triggered | weighted kappa 0.70 on the 40 repeats (exact agreement 0.85, no SHOW/SKIP flips) |
| D4: prevalence | not triggered | 33 relevant items among 160 |

"Cannot tell" means the data does not separate the systems; it does **not** mean they are equal. Plainly:

- Jev V2's ranking is better than chance on this reader's data. (Against the descriptive "newest first" comparator, the lenient AP and AUC intervals also exclude 0.)
- Jev V2 is **not shown to be better than** the LLM Stage 1 or than Jev V1. Its point estimates are nominally the *lowest* of
  the three model systems on AP and AUC, and the middle one on nDCG@16, all within noise.
- The primary set has only 5 SHOW items, so strict SHOW metrics are descriptive only.
- **All three model systems under-represent title-only items** near the top of the ranking (Jev V2 placed none in its top 16),
  although title-only items were 30% of the positives. Judging on a headline alone is a known weakness (see limitations).

The full aggregate report, including secondary metrics, stratification, model-only orderings and provenance, is in
[../benchmark_v2/results/benchmark_v2_report.md](../benchmark_v2/results/benchmark_v2_report.md). It names no item.
The tool's numbers were independently recomputed to four decimal places in a separate script.

### Deviations and honest notes

- The Jev runs were requested through the moving alias `jev-latest`, not pinned. Every answer came from `jev-1.13.0`, which is
  recorded in each stored result. The pre-declared plan was to pin.
- The evaluation code was written **after** the labels were frozen (it had to implement the methodology as written) and was
  committed before any model ran. A command-line decorator bug was fixed before any model call.
- A few exploratory analyses were run afterwards (a label-noise sensitivity check); they are marked as exploratory and decided
  nothing.

## What the benchmark cannot show

One reader (also the profile's author), one 60-day sample, popularity signals read with hindsight, one model version, one run,
and ordinary statistical uncertainty at n = 160 (roughly a 0.1 half-width on an AUC near 0.7). The corpus and labels are now
**spent** for the frozen system: any changed system needs a new corpus and a new benchmark version.

## What is published, and what is not

| Published | Not published, and why |
|---|---|
| Methodology (frozen), evaluation code, tests | The corpus, the labels, the raw model outputs and the collected item pool: together they describe one person's reading habits and opinions, and the outputs embed the reader's profile |
| The aggregate V2 report (no item is named) | The V1 report: it lists individual items next to model scores |
| Result tables in this file | The reader's interest profile |

## Reproducing it

**What you can reproduce.** The statistics and the tooling are deterministic and covered by tests that need no keys and no data:
`pytest tests/test_bench*` (about 240 tests: metrics, bootstrap and permutation code, the decision rules, the blinding
guarantees, the freeze and `verify` logic). That is the part of the benchmark a stranger can rerun today.

**What you cannot reproduce from this repository.** The reported V2 numbers. They depend on the private labels and corpus, and
the V2 corpus builder is tied to the original study (fixed historical window; it needs the private V1 item snapshot for its
absence check and repeats, and reads constants from `benchmark_v2/common.py`). Rerunning that exact study on someone else's
data is not a supported workflow.

**How to run the same kind of evaluation on your own data.** The V1 tooling (`benchmarks/`, [README](../benchmarks/README.md))
works from your own database: it snapshots items you have collected, has you label them blind, freezes the arms' raw outputs
and reports the same family of metrics with bootstrap intervals. It calls the Jev and Groq APIs (a few cents in total). To
follow the V2 discipline (freeze before labelling, fresh corpus, one run per system, decision rules fixed in advance), read
[../benchmark_v2/METHODOLOGY.md](../benchmark_v2/METHODOLOGY.md) and adapt the corpus builder's constants.
`python -m benchmark_v2 verify --evaluation` checks that nothing defining the system under test has changed since a freeze.
