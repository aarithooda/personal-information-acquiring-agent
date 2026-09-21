# Benchmark v2: a fresh, blind evaluation of the current Jev layer

Benchmark version: **v2**. The contract is [METHODOLOGY.md](METHODOLOGY.md); it was fixed before you see any item and must not change
afterwards. This file is the practical guide.

> **Status (release): evaluated, and the corpus is spent.** Labelling, the label freeze, the one run per system and the analysis
> are complete. The results are in [../docs/evaluation.md](../docs/evaluation.md) and the aggregate report is in
> [results/benchmark_v2_report.md](results/benchmark_v2_report.md). The corpus, labels, arm outputs and item pool
> (`benchmark_v2/data/`) describe one person's reading and opinions and are **not published**, so the numbers cannot be
> regenerated from this repository. The corpus builder is tied to the original study (a fixed historical window, and it needs
> the private Benchmark V1 snapshot). What follows is the original labeller's guide, kept because it is part of the protocol.
> Do not reuse this corpus or these labels to tune the system: a changed system needs a new benchmark version.

*Historical text, as it stood when labelling began (no longer current; see the status note above):*

**Where things stand when you start labelling:** the corpus (200 items) is built and frozen, the methodology is frozen, and **no model has
seen this corpus**. No Jev output, LLM output, score or rank exists for it. Your labels are what everything is later measured against.

## Label the 200 items

Double-click **`benchmark_v2\label.bat`**, or run:

```bash
.venv\Scripts\python.exe -m benchmark_v2 label
```

**Keys:** `y` = SHOW · `m` = MAYBE · `n` = SKIP · `o` = open the link · `b` = back one · `q` = save and quit (`1` `2` `3` also work).
Every label is saved to disk the moment you press the key, so you can stop at any time and run the same command to continue.
`python -m benchmark_v2 status` shows progress (counts only). There are **200 screens**; each item appears **once**.

| Label | Meaning |
|---|---|
| **SHOW** | I would want this surfaced in my briefing. |
| **MAYBE** | Interesting enough that I might want it, but not clearly worth a headline. |
| **SKIP** | I would not want this surfaced. |

**How to judge.** Judge each item as it appears on screen: the source label, the title and (up to 500 characters of) the text, which is exactly
what Jev would be sent. Ask *if this appeared in my briefing, would I be glad?* Go with your considered first reaction and use MAYBE when it is truly
in between. If the title alone is too vague you may press `o` to read the link (that is recorded); decide whether the item, *as shown*, deserved a spot.

**Rules that keep the result valid**
1. Label all 200 in one or several sittings. You may go back (`b`) and change a label; the latest one counts.
2. **Do not open any file in `benchmark_v2\data`** (they hold information you must not see), and do not run `analyze`, `arm`, `verify`, `summary` or any
   report command until your labels are frozen.
3. **You will not be told which items are repeats, and you must not try to find out.** Some items may look familiar. That is expected. Label each
   one as you feel *now*; do not try to recall an earlier label.
4. Do not ask anyone (including the assistant) whether an item is "one of the old ones", how a model scored something, or what others chose.
5. If something looks broken, stop and say so rather than working around it.

## When all 200 are labelled

```bash
.venv\Scripts\python.exe -m benchmark_v2 freeze-labels
```

This locks your labels (it refuses if any item is unlabelled, or if any model output already exists for this corpus). Then tell the assistant
"labels are frozen". Only after that are the models run, once each, on the frozen corpus, and the analysis exactly as METHODOLOGY.md specifies.

## Commands (for reference)

| Command | Who runs it | What it does |
|---|---|---|
| `python -m benchmark_v2 build` | assistant, once | collects the historical pool through PIA's sources, samples the corpus, writes it |
| `python -m benchmark_v2 summary` | assistant | distributions of the corpus (counts only, no item is named) |
| `python -m benchmark_v2 freeze` | assistant, once | writes FREEZE.json: corpus, methodology and production code are fixed |
| `python -m benchmark_v2 verify` | either | checks nothing has changed since the freeze (`--evaluation` once model output is allowed) |
| `python -m benchmark_v2 init` | assistant, once | creates the shuffled queue: one screen per item |
| `python -m benchmark_v2 label` | **you** | the labelling session |
| `python -m benchmark_v2 status` | you | progress, counts only |
| `python -m benchmark_v2 freeze-labels` | **you**, at the end | locks the labels |

## Files (all under `benchmark_v2\data`, git-ignored, do not open before the labels are frozen)

`items.json` the 200 items (facts only) · `manifest.json` INTERNAL: origins and repeat identities · `pool.db` INTERNAL: everything collected ·
`FREEZE.json` the freeze record · `queue.json` the shuffled order · `labels.jsonl` your labels (append-only) · `labels_frozen.json` the frozen labels.
