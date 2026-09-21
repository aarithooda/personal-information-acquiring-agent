# Benchmark v2 evaluation report

Frozen methodology `6a175d2dde6f` · corpus `9ab6d871ee47` (200 items) · labels `bf0f052330a3` · generated 2026-09-21T14:10:13.583045+00:00 at code `ea1272a`

Aggregate patterns only. The labels are the frozen human signal, not objective per-item truth; no item is named or explained here.

## Labels

Primary set (160 new items): {'SHOW': 5, 'MAYBE': 28, 'SKIP': 127} · SHOW+MAYBE = 33. All 200: {'SHOW': 6, 'MAYBE': 40, 'SKIP': 154}.

## Primary metrics

On the 160 new items, each arm ranked among them by PIA's production ranking (references by their own rule). 95% percentile bootstrap intervals (5000 resamples, seed 20260925).

| arm | P1 nDCG@16 | P2 AP lenient | P3 AUC lenient |
|---|---|---|---|
| jev-v2 | 0.362 [0.150, 0.559] | 0.375 [0.229, 0.532] | 0.627 [0.516, 0.736] |
| llm-stage1 | 0.316 [0.121, 0.523] | 0.387 [0.242, 0.549] | 0.670 [0.555, 0.774] |
| jev-v1 | 0.392 [0.169, 0.580] | 0.396 [0.245, 0.555] | 0.650 [0.541, 0.757] |
| R1-newest | 0.142 [0.000, 0.322] | 0.203 [0.125, 0.303] | 0.417 [0.314, 0.522] |
| R2-text-first | 0.233 [0.031, 0.402] | 0.244 [0.152, 0.361] | 0.503 [0.384, 0.619] |
| R3-popularity | 0.335 [0.109, 0.536] | 0.315 [0.188, 0.468] | 0.561 [0.446, 0.677] |

## Above-chance test

One-sided permutation test, 10000 shuffles, seed 20260926; p = (1 + shuffles at least as good) / (1 + shuffles). D1 needs both P2 and P3 below 0.025.

| arm | P1 p | P2 p | P3 p |
|---|---|---|---|
| jev-v2 | 0.0283 | 0.0035 | 0.0131 |
| llm-stage1 | 0.0586 | 0.0024 | 0.0016 |
| jev-v1 | 0.0168 | 0.0018 | 0.0049 |
| R1-newest | 0.5286 | 0.7384 | 0.9290 |
| R2-text-first | 0.1917 | 0.3140 | 0.4811 |
| R3-popularity | 0.0447 | 0.0297 | 0.1435 |

## Comparisons

Paired bootstrap difference, `jev-v2` minus the comparator, on the same resamples. **Decision comparators: llm-stage1 and R2-text-first**; the rest are descriptive.

| comparator | P1 nDCG@16 | P2 AP lenient | P3 AUC lenient |
|---|---|---|---|
| llm-stage1 (decision) | 0.043 [-0.099, 0.186] · ahead 0.73 | -0.014 [-0.107, 0.080] · ahead 0.38 | -0.043 [-0.131, 0.039] · ahead 0.16 |
| jev-v1 | -0.019 [-0.097, 0.056] · ahead 0.27 | -0.021 [-0.063, 0.022] · ahead 0.14 | -0.022 [-0.053, 0.008] · ahead 0.08 |
| R1-newest | 0.219 [-0.043, 0.466] · ahead 0.95 | 0.173 [0.009, 0.337] · ahead 0.98 | 0.212 [0.047, 0.370] · ahead 0.99 |
| R2-text-first (decision) | 0.152 [-0.107, 0.409] · ahead 0.87 | 0.131 [-0.028, 0.290] · ahead 0.95 | 0.126 [-0.011, 0.251] · ahead 0.97 |
| R3-popularity | 0.038 [-0.203, 0.275] · ahead 0.62 | 0.057 [-0.097, 0.196] · ahead 0.78 | 0.065 [-0.078, 0.197] · ahead 0.83 |

## Repeat-label reliability

40 v1 items re-labelled in v2 (short interval, possible memory; **not** independent test items). Exact agreement 0.85 · weighted kappa 0.70 (floor 0.4) · SHOW/SKIP flips 0 · SHOW+MAYBE rate v1 0.38 to v2 0.33 · noise reference (v1 label as a ranker of the v2 label) AUC lenient 0.91.

| v1 label \ v2 label | SHOW | MAYBE | SKIP |
|---|---|---|---|
| SKIP | 0 | 1 | 24 |
| MAYBE | 1 | 10 | 3 |
| SHOW | 0 | 1 | 0 |

## Title-only analysis

Title-only 58, has text 102. Positive rate: title-only 0.17, has text 0.23. Title-only share of the corpus 0.36, of the positives 0.30. RR = title-only share of an arm's top 32 / title-only share of positives; flag if RR < 0.5.

| arm | top-16 title-only share | top-32 share | RR | flag | AUC title-only | AUC has text |
|---|---|---|---|---|---|---|
| jev-v2 | 0.00 | 0.09 | 0.31 | UNDER-REPRESENTED | 0.75 | 0.61 |
| llm-stage1 | 0.19 | 0.12 | 0.41 | UNDER-REPRESENTED | 0.77 | 0.58 |
| jev-v1 | 0.06 | 0.03 | 0.10 | UNDER-REPRESENTED | 0.77 | 0.64 |
| R1-newest | 0.38 | 0.44 | 1.44 |  | 0.26 | 0.48 |
| R2-text-first | 0.00 | 0.00 | 0.00 | UNDER-REPRESENTED | 0.26 | 0.48 |
| R3-popularity | 0.56 | 0.56 | 1.86 |  | 0.64 | 0.58 |

## Stratification

Within-stratum AUC / AP (lenient) per arm, and the share of the stratum's positives inside the arm's global top 32. Strata with fewer than 5 SHOW+MAYBE are marked (few) and support no inference.

### source

| group | n | S/M/K | pos rate | jev-v2: AUC · top32 | llm-stage1: AUC · top32 | R2-text-first: AUC · top32 |
|---|---|---|---|---|---|---|
| arxiv | 57 | 1/9/47 | 0.18 | 0.60 · 0.10 | 0.66 · 0.20 | 0.60 · 0.40 |
| github (few) | 12 | 0/3/9 | 0.25 | 0.78 · 0.67 | 0.59 · 0.67 | 0.48 · 0.33 |
| hf_blog (few) | 2 | 0/1/1 | 0.50 | 0.00 · 0.00 | 1.00 · 0.00 | 0.00 · 0.00 |
| hf_papers | 31 | 0/8/23 | 0.26 | 0.71 · 0.75 | 0.57 · 0.62 | 0.29 · 0.00 |
| hn | 56 | 3/6/47 | 0.16 | 0.77 · 0.11 | 0.77 · 0.22 | 0.26 · 0.00 |
| openai (few) | 2 | 1/1/0 | 1.00 | n/a · 0.00 | n/a · 0.00 | n/a · 0.50 |

### native_category

| group | n | S/M/K | pos rate | jev-v2: AUC · top32 | llm-stage1: AUC · top32 | R2-text-first: AUC · top32 |
|---|---|---|---|---|---|---|
| (none: Hacker News and HF papers have no native category) | 87 | 3/14/70 | 0.20 | 0.72 · 0.41 | 0.73 · 0.41 | 0.45 · 0.00 |
| arxiv:cs.AI | 26 | 1/4/21 | 0.19 | 0.57 · 0.20 | 0.68 · 0.40 | 0.70 · 0.40 |
| arxiv:cs.CL (few) | 4 | 0/2/2 | 0.50 | 1.00 · 0.00 | 0.75 · 0.00 | 0.25 · 0.50 |
| arxiv:cs.CR (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| arxiv:cs.CV (few) | 5 | 0/0/5 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| arxiv:cs.DC (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| arxiv:cs.ET (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| arxiv:cs.IR (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| arxiv:cs.LG (few) | 8 | 0/1/7 | 0.12 | 0.14 · 0.00 | 0.71 · 0.00 | 0.86 · 1.00 |
| arxiv:cs.LO (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| arxiv:cs.NI (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| arxiv:cs.RO (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| arxiv:cs.SD (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| arxiv:cs.SE (few) | 4 | 0/2/2 | 0.50 | 0.50 · 0.00 | 1.00 · 0.00 | 1.00 · 0.00 |
| arxiv:physics.plasm-ph (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| arxiv:stat.AP (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| github:C# (few) | 1 | 0/1/0 | 1.00 | n/a · 0.00 | n/a · 0.00 | n/a · 1.00 |
| github:Go (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| github:HTML (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| github:Java (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| github:JavaScript (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| github:Python (few) | 4 | 0/1/3 | 0.25 | 0.67 · 1.00 | 0.33 · 1.00 | 1.00 · 0.00 |
| github:Rust (few) | 1 | 0/0/1 | 0.00 | n/a · n/a | n/a · n/a | n/a · n/a |
| github:TypeScript (few) | 2 | 0/1/1 | 0.50 | 1.00 · 1.00 | 1.00 · 1.00 | 0.00 · 0.00 |
| rss:hf_blog (few) | 2 | 0/1/1 | 0.50 | 0.00 · 0.00 | 1.00 · 0.00 | 0.00 · 0.00 |
| rss:openai (few) | 2 | 1/1/0 | 1.00 | n/a · 0.00 | n/a · 0.00 | n/a · 0.50 |

### title_only

| group | n | S/M/K | pos rate | jev-v2: AUC · top32 | llm-stage1: AUC · top32 | R2-text-first: AUC · top32 |
|---|---|---|---|---|---|---|
| has text | 102 | 2/21/79 | 0.23 | 0.61 · 0.39 | 0.58 · 0.39 | 0.48 · 0.26 |
| title-only | 58 | 3/7/48 | 0.17 | 0.75 · 0.10 | 0.77 · 0.20 | 0.26 · 0.00 |

### model_category:jev-v2

| group | n | S/M/K | pos rate | jev-v2: AUC · top32 | llm-stage1: AUC · top32 | R2-text-first: AUC · top32 |
|---|---|---|---|---|---|---|
| ai | 113 | 4/26/83 | 0.27 | 0.53 · 0.33 | 0.61 · 0.33 | 0.41 · 0.17 |
| other (few) | 20 | 0/1/19 | 0.05 | 0.79 · 0.00 | 0.16 · 0.00 | 0.16 · 0.00 |
| research (few) | 6 | 1/0/5 | 0.17 | 1.00 · 0.00 | 1.00 · 1.00 | 0.20 · 0.00 |
| software (few) | 21 | 0/1/20 | 0.05 | 0.90 · 0.00 | 0.70 · 0.00 | 0.95 · 1.00 |

### model_category:llm-stage1

| group | n | S/M/K | pos rate | jev-v2: AUC · top32 | llm-stage1: AUC · top32 | R2-text-first: AUC · top32 |
|---|---|---|---|---|---|---|
| ai | 89 | 1/21/67 | 0.25 | 0.53 · 0.32 | 0.60 · 0.32 | 0.40 · 0.23 |
| other (few) | 34 | 2/2/30 | 0.12 | 0.60 · 0.00 | 0.33 · 0.00 | 0.59 · 0.25 |
| research (few) | 10 | 1/1/8 | 0.20 | 0.69 · 0.00 | 0.56 · 0.50 | 0.31 · 0.00 |
| software | 27 | 1/4/22 | 0.19 | 0.75 · 0.60 | 0.93 · 0.60 | 0.42 · 0.00 |

## Secondary metrics (descriptive only)

| metric | jev-v2 | llm-stage1 | jev-v1 | R1-newest | R2-text-first | R3-popularity |
|---|---|---|---|---|---|---|
| ndcg@4 | 0.402 | 0.377 | 0.402 | 0.195 | 0.195 | 0.279 |
| ndcg@10 | 0.367 | 0.350 | 0.406 | 0.172 | 0.176 | 0.332 |
| ndcg@16 | 0.362 | 0.316 | 0.392 | 0.142 | 0.233 | 0.335 |
| ndcg@32 | 0.315 | 0.335 | 0.320 | 0.157 | 0.203 | 0.292 |
| precision_lenient@4 | 0.750 | 0.750 | 0.750 | 0.250 | 0.250 | 0.500 |
| precision_lenient@10 | 0.500 | 0.500 | 0.600 | 0.200 | 0.200 | 0.500 |
| precision_lenient@16 | 0.438 | 0.375 | 0.500 | 0.125 | 0.250 | 0.375 |
| precision_lenient@32 | 0.312 | 0.344 | 0.312 | 0.125 | 0.188 | 0.250 |
| recall_lenient@4 | 0.091 | 0.091 | 0.091 | 0.030 | 0.030 | 0.061 |
| recall_lenient@10 | 0.152 | 0.152 | 0.182 | 0.061 | 0.061 | 0.152 |
| recall_lenient@16 | 0.212 | 0.182 | 0.242 | 0.061 | 0.121 | 0.182 |
| recall_lenient@32 | 0.303 | 0.333 | 0.303 | 0.121 | 0.182 | 0.242 |
| capture_lenient@4 | 0.750 | 0.750 | 0.750 | 0.250 | 0.250 | 0.500 |
| capture_lenient@10 | 0.500 | 0.500 | 0.600 | 0.200 | 0.200 | 0.500 |
| capture_lenient@16 | 0.438 | 0.375 | 0.500 | 0.125 | 0.250 | 0.375 |
| capture_lenient@32 | 0.312 | 0.344 | 0.312 | 0.125 | 0.188 | 0.250 |
| ap | 0.032 | 0.038 | 0.030 | 0.031 | 0.037 | 0.054 |
| auc_show_vs_skip | 0.457 | 0.472 | 0.431 | 0.370 | 0.354 | 0.565 |
| spearman | 0.168 | 0.227 | 0.197 | -0.119 | -0.004 | 0.086 |
| recall@4 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| recall@10 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| recall@16 | 0.000 | 0.000 | 0.000 | 0.000 | 0.200 | 0.200 |
| recall@32 | 0.000 | 0.200 | 0.000 | 0.200 | 0.200 | 0.400 |
| precision@4 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| precision@10 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| precision@16 | 0.000 | 0.000 | 0.000 | 0.000 | 0.062 | 0.062 |
| precision@32 | 0.000 | 0.031 | 0.000 | 0.031 | 0.031 | 0.062 |

Model-only order (model score alone, no popularity or cross-source boost), primary metrics:

| arm | P1 | P2 | P3 |
|---|---|---|---|
| jev-v2 | 0.204 | 0.289 | 0.609 |
| llm-stage1 | 0.185 | 0.302 | 0.616 |
| jev-v1 | 0.141 | 0.293 | 0.635 |

All 200 items ranked together (secondary; includes the 40 repeats), primary metrics:

| arm | P1 | P2 | P3 |
|---|---|---|---|
| jev-v2 | 0.348 | 0.364 | 0.616 |
| llm-stage1 | 0.349 | 0.407 | 0.678 |
| jev-v1 | 0.314 | 0.354 | 0.605 |
| R1-newest | 0.272 | 0.284 | 0.506 |
| R2-text-first | 0.361 | 0.362 | 0.568 |
| R3-popularity | 0.251 | 0.299 | 0.568 |

## Verdicts (METHODOLOGY section 14)

- **D1 above chance:** Ranking skill demonstrated
- **D2 versus llm-stage1:** cannot tell
- **D2 versus R2-text-first:** cannot tell
- **D3 label noise:** weighted kappa 0.70; applies: False
- **D4 prevalence:** 33 SHOW+MAYBE among the primary items; underpowered: False

## Provenance

| arm | kind | model id(s) | question set / prompt | jev requested | profile | derivation | ranking | commit | input tokens | seconds (last leg) | created |
|---|---|---|---|---|---|---|---|---|---|---|---|
| jev-v2 | jev | jev-1.13.0 | jev-triage-v2 | jev-latest | 3c89ca453a9b | jev-triage-v2 | 0faf08359769 | ea1272a | 1356172 | 90.2 | 2026-09-21T14:03 |
| llm-stage1 | llm | openai/gpt-oss-20b | stage1-v2 |  |  |  | 0faf08359769 | ea1272a |  | 271.5 | 2026-09-21T14:04 |
| jev-v1 | jev | jev-1.13.0 | jev-triage-v1 | jev-latest | 3c89ca453a9b | jev-triage-v1 | 0faf08359769 | ea1272a | 887993 | 23.2 | 2026-09-21T14:09 |
