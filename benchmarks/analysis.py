"""Score arms against the human labels and produce a report.

Input: the frozen snapshot, a label set, and one or more arms. Output: a plain-JSON result (for machines and for
comparing runs) and a Markdown rendering (for people). Nothing here calls a model or the network.

HOW AN ARM IS RANKED. Through PIA's own ranking (`arms.production_order`): filter, popularity percentile, cross-source
boost, recency tie-break. That is what the reader would actually be shown, so it is the primary number. Each arm is
also reported "model only" (ordered by the model's score alone) so the effect of the ranker on top of the model
can be separated from the effect of the model.

WHAT IS COMPARED. Every arm is scored against the SAME labels and, for uncertainty, on the SAME bootstrap resamples,
so a difference between two arms is a paired difference, not two independently noisy numbers.
"""

import statistics
from dataclasses import dataclass, field
from datetime import datetime

from benchmarks.arms import Arm, Ordering, code_version, production_order
from benchmarks.common import BENCHMARK_VERSION, BenchmarkError
from benchmarks.labelset import GRADE, LabelSet, retest_summary
from benchmarks.metrics import DEFAULT_KS, MAYBE, SHOW, SKIP, bootstrap_metrics, effective_k, paired_delta, percentile_ci, summarize
from benchmarks.snapshot import Snapshot, is_title_only

WILDCARD_CANDIDATE = 0.5  # Jev's "strong wildcard" probability at or above which an item counts as a discovery candidate
MIN_SHOW_PER_GROUP = 5  # a group with fewer SHOW items than this is flagged: its recall is too coarse to interpret
NAME = {SHOW: "SHOW", MAYBE: "MAYBE", SKIP: "SKIP"}
FEATURES = ("interest_match", "substance", "wildcard", "buildable", "low_value", "too_little_info")


@dataclass
class Config:
    ks: tuple[int, ...] = DEFAULT_KS
    n_boot: int = 1000  # 0 skips the intervals (fast, for tests and quick looks)
    seed: int = 0
    baseline: str | None = None  # the arm the others are compared against; default: the first
    reference_arm: str | None = None  # whose category / wildcard answers define the groups; default: the first Jev arm
    fn_k: int = 16  # a SHOW item ranked below this is a "miss" (16 = the editor's shortlist for a 5-day gap)
    strata_ks: tuple[int, ...] = (16, 32)
    top_disagreements: int = 10
    extra: dict = field(default_factory=dict)


def fmt(value: float | None, places: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{places}f}"


def _score(record: dict | None) -> float | None:
    """The model's score on the 1-5 scale, as the ranker sees it."""
    if record is None:
        return None
    return 1 + 4 * record["relevance"] if record["relevance"] is not None else float(record["importance"])


# ---------- validation ----------


def _validate(snapshot: Snapshot, labels: LabelSet, arms: dict[str, Arm], config: Config) -> None:
    if not arms:
        raise BenchmarkError("give at least one arm to analyse")
    for name, arm in arms.items():
        if arm.meta.get("snapshot_id") != snapshot.snapshot_id:
            raise BenchmarkError(f"arm {name!r} was produced from a different snapshot ({arm.meta.get('snapshot_id')}, not {snapshot.snapshot_id})")
    unknown = set(labels.grades) - {item["id"] for item in snapshot.items}
    if unknown:
        raise BenchmarkError(f"{len(unknown)} labelled items are not in the snapshot (for example {sorted(unknown)[0]})")
    for option in (config.baseline, config.reference_arm):
        if option is not None and option not in arms:
            raise BenchmarkError(f"baseline/reference arm {option!r} is not among the arms: {', '.join(arms)}")


# ---------- groups ----------


def _groups(snapshot: Snapshot, arms: dict[str, Arm], config: Config) -> dict[str, dict[str, list[int]]]:
    """dimension -> group name -> item ids. Category and wildcard come from one reference arm (the first Jev arm by
    default), because groups must mean the same thing when arms are compared."""
    reference = arms[config.reference_arm] if config.reference_arm else next((a for a in arms.values() if a.kind == "jev"), next(iter(arms.values())))
    groups: dict[str, dict[str, list[int]]] = {"title_only": {}, "source": {}, "category": {}}
    if reference.kind == "jev":
        groups["wildcard_candidate"] = {}
    for item in snapshot.items:
        record = reference.records.get(item["id"])
        placement = {
            "title_only": "title-only" if is_title_only(item) else "has text",
            "source": item["source"],
            "category": record["category"] if record else "unscored",
        }
        if reference.kind == "jev":
            answers = ((record or {}).get("details") or {}).get("answers")
            is_wildcard = bool(answers) and answers["wildcard"]["noul"] >= WILDCARD_CANDIDATE
            placement["wildcard_candidate"] = "wildcard candidate" if is_wildcard else "not a wildcard"
        for dimension, group in placement.items():
            groups[dimension].setdefault(group, []).append(item["id"])
    return {d: dict(sorted(g.items())) for d, g in groups.items()}


def _group_cells(groups, labels: LabelSet, orders: dict[str, Ordering], config: Config, n_items: int) -> dict:
    positions = {name: order.positions() for name, order in orders.items()}
    out: dict = {}
    for dimension, by_group in groups.items():
        out[dimension] = {}
        for group, ids in by_group.items():
            labelled = [i for i in ids if i in labels.grades]
            shows = [i for i in labelled if labels.grades[i] == SHOW]
            cell = {
                "n": len(ids),
                "n_labelled": len(labelled),
                "show": len(shows),
                "maybe": sum(labels.grades[i] == MAYBE for i in labelled),
                "skip": sum(labels.grades[i] == SKIP for i in labelled),
                "show_rate": len(shows) / len(labelled) if labelled else None,
                "too_few": len(shows) < MIN_SHOW_PER_GROUP,
                "arms": {},
            }
            for name, pos in positions.items():
                in_order = sorted(labelled, key=pos.__getitem__)
                per_arm = {
                    # Of this group's SHOW items, how many made the arm's global top K? Uses positions in the full
                    # ranking, so it stays unbiased when only some items are labelled.
                    f"recall@{k}": (sum(pos[i] < k for i in shows) / len(shows) if shows else None)
                    for k in config.strata_ks
                }
                per_arm["auc_show_vs_skip"] = summarize([labels.grades[i] for i in in_order], ks=(1,))["auc_show_vs_skip"]
                per_arm["mean_rank_percentile_of_show"] = statistics.fmean((pos[i] + 1) / n_items for i in shows) if shows else None
                cell["arms"][name] = per_arm
            out[dimension][group] = cell
    return out


# ---------- misses, false alarms, disagreements ----------


def _why_missed(item_id: int, order: Ordering, k: int) -> str:
    if item_id in order.filtered:
        return f"never shown: {order.filtered[item_id]}"
    if item_id in order.unscored:
        return "no Stage 1 result"
    if order.model_only_positions()[item_id] < k:
        return "popularity or cross-source boost pushed it below the cut"
    return "low model score"


def _row(item: dict, arm: Arm, order: Ordering, labels: LabelSet) -> dict:
    record = arm.records.get(item["id"])
    features = ((record or {}).get("details") or {}).get("features")
    return {
        "item_id": item["id"],
        "title": item["title"],
        "source": item["source"],
        "title_only": is_title_only(item),
        "label": NAME.get(labels.grades.get(item["id"])),
        "rank": order.positions()[item["id"]] + 1,
        "model_only_rank": order.model_only_positions()[item["id"]] + 1,
        "score": _score(record),
        "category": record["category"] if record else None,
        "features": {f: features[f] for f in FEATURES if f in features} if features else None,
    }


def _misses(snapshot: Snapshot, labels: LabelSet, arm: Arm, order: Ordering, k: int) -> dict:
    by_id = snapshot.by_id()
    shows = [i for i, g in labels.grades.items() if g == SHOW]
    missed = sorted((i for i in shows if order.positions()[i] >= k), key=order.positions().get)
    rows = [{**_row(by_id[i], arm, order, labels), "why": _why_missed(i, order, k)} for i in missed]
    by_reason: dict[str, int] = {}
    for row in rows:
        by_reason[row["why"]] = by_reason.get(row["why"], 0) + 1
    return {"k": k, "n_show": len(shows), "n_missed": len(rows), "by_reason": by_reason, "rows": rows}


def _false_alarms(snapshot: Snapshot, labels: LabelSet, arm: Arm, order: Ordering, k: int) -> dict:
    by_id = snapshot.by_id()
    top = [i for i in order.ranked[:k] if i in labels.grades]
    wrong = [i for i in top if labels.grades[i] == SKIP]
    return {"k": k, "n_in_top_k": len(top), "rows": [_row(by_id[i], arm, order, labels) for i in wrong]}


def _disagreements(snapshot: Snapshot, labels: LabelSet, baseline: str, arm: str, arms: dict[str, Arm], orders: dict[str, Ordering], config: Config) -> dict:
    by_id = snapshot.by_id()
    pos_a, pos_b = orders[baseline].positions(), orders[arm].positions()
    n = len(snapshot.items)
    spearman = 1 - 6 * sum((pos_a[i] - pos_b[i]) ** 2 for i in pos_a) / (n * (n * n - 1)) if n > 1 else None  # positions are unique
    k = config.fn_k
    overlap = len(set(orders[baseline].ranked[:k]) & set(orders[arm].ranked[:k])) / k

    def row(i: int) -> dict:
        item = by_id[i]
        return {
            "item_id": i,
            "title": item["title"],
            "source": item["source"],
            "title_only": is_title_only(item),
            "baseline_rank": pos_a[i] + 1,
            "arm_rank": pos_b[i] + 1,
            "label": NAME.get(labels.grades.get(i)),
            "baseline_score": _score(arms[baseline].records.get(i)),
            "arm_score": _score(arms[arm].records.get(i)),
        }

    gap = {i: pos_b[i] - pos_a[i] for i in pos_a}  # positive: the baseline ranks the item better than the other arm does
    n_top = config.top_disagreements
    return {
        "baseline": baseline,
        "arm": arm,
        "spearman": spearman,
        "top_k": k,
        "top_k_overlap": overlap,
        "baseline_higher": [row(i) for i in sorted((i for i in gap if gap[i] > 0), key=lambda i: -gap[i])[:n_top]],
        "arm_higher": [row(i) for i in sorted((i for i in gap if gap[i] < 0), key=lambda i: gap[i])[:n_top]],
    }


# ---------- the analysis ----------


def analyze(snapshot: Snapshot, labels: LabelSet, arms: dict[str, Arm], *, config: Config = Config(), now: datetime, code=code_version) -> dict:
    _validate(snapshot, labels, arms, config)
    baseline = config.baseline or next(iter(arms))
    n_items, labelled = len(snapshot.items), sorted(labels.grades)
    partial = len(labelled) < labels.n_total
    n_total = labels.n_total if partial else None
    orders = {name: production_order(snapshot, arm) for name, arm in arms.items()}
    positions = {name: order.positions() for name, order in orders.items()}
    grades_in_order = lambda pos: [labels.grades[i] for i in sorted(labelled, key=pos.__getitem__)]  # noqa: E731

    metric_names = [m for m in summarize([0, 1, 2], config.ks) if not m.startswith("hits@")]
    samples = (
        bootstrap_metrics(labelled, labels.grades, positions, metric_names, ks=config.ks, n_total=n_total, n_boot=config.n_boot, seed=config.seed)
        if config.n_boot
        else None
    )

    result_arms = {}
    for name, arm in arms.items():
        order = orders[name]
        point = summarize(grades_in_order(positions[name]), config.ks, n_total)
        model_only = summarize(grades_in_order(order.model_only_positions()), config.ks, n_total)
        result_arms[name] = {
            "kind": arm.kind,
            "coverage": {"n_items": n_items, "scored": n_items - len(order.unscored), "unscored": len(order.unscored), "never_shown": len(order.filtered)},
            "metrics": {m: {"value": v, "ci": list(percentile_ci(samples[name][m])) if samples and m in samples[name] else None} for m, v in point.items()},
            "model_only": model_only,
            "by_label": {
                NAME[g]: {
                    "n": len(ids),
                    "median_rank": statistics.median(positions[name][i] + 1 for i in ids) if ids else None,
                    "median_score": statistics.median(s for i in ids if (s := _score(arm.records.get(i))) is not None) if any(arm.records.get(i) for i in ids) else None,
                }
                for g in (SHOW, MAYBE, SKIP)
                for ids in [[i for i in labelled if labels.grades[i] == g]]
            },
        }

    comparisons = []
    for name in arms:
        if name == baseline:
            continue
        if samples:
            deltas = {m: paired_delta(samples[baseline][m], samples[name][m]) for m in metric_names}
        else:
            deltas = {
                m: {"n": 0, "mean": (b - a) if (a := result_arms[baseline]["metrics"][m]["value"]) is not None and (b := result_arms[name]["metrics"][m]["value"]) is not None else None, "ci": None, "p_b_better": None, "p_tie": None}
                for m in metric_names
            }
        comparisons.append({"baseline": baseline, "arm": name, "metrics": {m: {**d, "ci": list(d["ci"]) if d["ci"] else None} for m, d in deltas.items()}})

    groups = _groups(snapshot, arms, config)
    hide_items = partial  # naming items next to model scores would influence the labels still to be made
    misses = {name: _misses(snapshot, labels, arms[name], orders[name], config.fn_k) for name in arms}
    false_alarms = {name: _false_alarms(snapshot, labels, arms[name], orders[name], config.fn_k) for name in arms}
    disagreements = [_disagreements(snapshot, labels, baseline, name, arms, orders, config) for name in arms if name != baseline]
    if hide_items:
        for miss in misses.values():
            miss.update(rows=[], n_missed=None, by_reason={})
        for alarm in false_alarms.values():
            alarm.update(rows=[], n_in_top_k=None)
        for d in disagreements:
            d.update(baseline_higher=[], arm_higher=[])
    return {
        "meta": {
            "benchmark_version": BENCHMARK_VERSION,
            "generated_at": now.isoformat(),
            "code": code(),
            "snapshot_id": snapshot.snapshot_id,
            "labels_hash": labels.labels_hash,
            "labels_source": labels.source,
            "n_items": n_items,
            "n_labelled": len(labelled),
            "label_counts": labels.counts,
            "preliminary": partial or labels.source != "frozen",
            "items_hidden": hide_items,
            "ks": list(config.ks),
            "effective_ks": {str(k): effective_k(k, len(labelled), labels.n_total) for k in config.ks},
            "n_boot": config.n_boot,
            "seed": config.seed,
            "baseline": baseline,
            "fn_k": config.fn_k,
            "arms": {
                name: {key: arm.meta.get(key) for key in ("kind", "models", "prompt_version", "question_set", "profile_hash", "code", "created_at", "rescored_from", "elapsed_seconds", "input_tokens")}
                for name, arm in arms.items()
            },
        },
        "labels": {"counts": labels.counts, "retest": retest_summary(labels.retest)},
        "arms": result_arms,
        "comparisons": comparisons,
        "strata": _group_cells(groups, labels, orders, config, n_items),
        "false_negatives": misses,
        "false_positives": false_alarms,
        "disagreements": disagreements,
    }


# ---------- the Markdown report ----------


def _table(headers: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines + [""]


def _cell(entry: dict) -> str:
    value, ci = entry["value"], entry["ci"]
    return fmt(value) if not ci or ci[0] is None else f"{fmt(value)} [{fmt(ci[0])}-{fmt(ci[1])}]"


def _metric_order(ks: list[int], lenient: bool) -> list[str]:
    if lenient:
        return [f"{m}@{k}" for k in ks for m in ("precision_lenient", "recall_lenient")] + ["ap_lenient"]
    return [f"{m}@{k}" for k in ks for m in ("precision", "recall", "capture", "ndcg")] + ["ap", "auc_show_vs_skip", "auc_show_vs_rest", "spearman"]


def _row_line(row: dict, extra: list[str]) -> list:
    return [row["rank"], row["title"][:70], row["source"], "yes" if row["title_only"] else "", *[row.get(k, "") for k in extra]]


def render_markdown(result: dict) -> str:
    meta, out = result["meta"], ["# PIA benchmark report", ""]
    if meta["preliminary"]:
        reason = (
            f"Only {meta['n_labelled']} of {meta['n_items']} items are labelled" if meta["n_labelled"] < meta["n_items"] else "The labels are not frozen"
        )
        out += [f"> **PRELIMINARY.** {reason}. Numbers are noisier than they look and the labels can still change. Do not act on them.", ""]
    out += [
        f"- Benchmark version: {meta['benchmark_version']} (reports from different versions must not be compared)",
        f"- Snapshot `{meta['snapshot_id']}` ({meta['n_items']} items) · labels `{meta['labels_hash']}` ({meta['labels_source']}, {meta['n_labelled']} labelled: "
        + ", ".join(f"{v} {k}" for k, v in meta["label_counts"].items())
        + ")",
        f"- Generated {meta['generated_at']} at code `{meta['code']['commit']}`{' (uncommitted changes)' if meta['code']['dirty'] else ''} · bootstrap {meta['n_boot']} resamples, seed {meta['seed']}",
        f"- Baseline for comparisons: `{meta['baseline']}`. K values: {', '.join(map(str, meta['ks']))}"
        + (f" (labelled subset, so K is scaled to {meta['effective_ks']})" if meta["n_labelled"] < meta["n_items"] else ""),
        "",
    ]
    names = list(result["arms"])

    out += ["## Headline metrics", "", "Production ranking (what PIA would actually show). Values in brackets are 95% bootstrap intervals.", ""]
    strict = _metric_order(meta["ks"], False)
    out += _table(["metric", *names], [[m, *[_cell(result["arms"][n]["metrics"][m]) for n in names]] for m in strict])
    out += ["Counting MAYBE as a hit too (lenient):", ""]
    out += _table(["metric", *names], [[m, *[_cell(result["arms"][n]["metrics"][m]) for n in names]] for m in _metric_order(meta["ks"], True)])
    out += ["Model score only, without popularity or cross-source boost:", ""]
    out += _table(["metric", *names], [[m, *[fmt(result["arms"][n]["model_only"][m]) for n in names]] for m in ("ap", "auc_show_vs_skip", *[f"ndcg@{k}" for k in meta["ks"]], *[f"recall@{k}" for k in meta["ks"]])])
    out += ["Where each kind of item landed (median rank; 1 = best):", ""]
    out += _table(
        ["arm", *[f"{label} (n)" for label in ("SHOW", "MAYBE", "SKIP")]],
        [[n, *[f"{fmt(result['arms'][n]['by_label'][lab]['median_rank'], 0)} ({result['arms'][n]['by_label'][lab]['n']})" for lab in ("SHOW", "MAYBE", "SKIP")]] for n in names],
    )
    coverage = [f"`{n}`: {result['arms'][n]['coverage']['scored']}/{result['arms'][n]['coverage']['n_items']} items scored, {result['arms'][n]['coverage']['never_shown']} can never be shown" for n in names]
    out += ["Coverage: " + "; ".join(coverage), ""]

    out += ["## Comparison with the baseline", "", "Paired differences (arm minus baseline) on the same resamples. `P(better)` is the share of resamples in which the arm beat the baseline.", ""]
    if not result["comparisons"]:
        out += ["Only one arm: nothing to compare.", ""]
    for comparison in result["comparisons"]:
        out += [f"### `{comparison['arm']}` vs `{comparison['baseline']}`", ""]
        rows = []
        for m in strict:
            d = comparison["metrics"][m]
            ci = f"[{fmt(d['ci'][0], 2)} to {fmt(d['ci'][1], 2)}]" if d["ci"] and d["ci"][0] is not None else "n/a"
            rows.append([m, fmt(d["mean"]), ci, fmt(d["p_b_better"])])
        out += _table(["metric", "difference", "95% interval", "P(better)"], rows)

    out += ["## Performance by group", "", f"`recall@K` here = of the group's SHOW items, the share that reached the arm's overall top K. Groups with fewer than {MIN_SHOW_PER_GROUP} SHOW items are marked (few): too coarse to interpret.", ""]
    strata_ks = sorted({int(key.split("@")[1]) for cell in next(iter(next(iter(result["strata"].values())).values()))["arms"].values() for key in cell if key.startswith("recall@")})
    for dimension, cells in result["strata"].items():
        out += [f"### By {dimension.replace('_', ' ')}", ""]
        headers = ["group", "items", "SHOW", "MAYBE", "SKIP", *[f"{n} recall@{k}" for k in strata_ks for n in names], *[f"{n} AUC" for n in names]]
        rows = [
            [
                group + (" (few)" if cell["too_few"] else ""),
                cell["n"],
                cell["show"],
                cell["maybe"],
                cell["skip"],
                *[fmt(cell["arms"][n][f"recall@{k}"]) for k in strata_ks for n in names],
                *[fmt(cell["arms"][n]["auc_show_vs_skip"]) for n in names],
            ]
            for group, cell in cells.items()
        ]
        out += _table(headers, rows)

    misses = result["false_negatives"]
    withheld = "Withheld until every item is labelled: naming items next to model scores could influence the labels still to be made."
    out += ["## Missed SHOW items (false negatives)", "", withheld if meta["items_hidden"] else f"SHOW items ranked below {meta['fn_k']}: the reader wanted them and would not have seen them.", ""]
    for name in [] if meta["items_hidden"] else names:
        fn = misses[name]
        reasons = ", ".join(f"{count} × {reason}" for reason, count in fn["by_reason"].items()) or "none"
        out += [f"### `{name}`: {fn['n_missed']} of {fn['n_show']} SHOW items missed ({reasons})", ""]
        if fn["rows"]:
            out += _table(["rank", "title", "source", "title-only", "why", "score", "features"], [
                [*_row_line(r, [])[:4], r["why"], fmt(r["score"]), ", ".join(f"{k} {fmt(v)}" for k, v in (r["features"] or {}).items())] for r in fn["rows"]
            ])
    out += ["## Wrongly surfaced (false positives)", "", withheld if meta["items_hidden"] else f"SKIP items inside the top {meta['fn_k']}.", ""]
    for name in [] if meta["items_hidden"] else names:
        fp = result["false_positives"][name]
        out += [f"### `{name}`: {len(fp['rows'])} of {fp['n_in_top_k']} labelled items in the top {fp['k']} were SKIP", ""]
        if fp["rows"]:
            out += _table(["rank", "title", "source", "title-only", "score"], [[*_row_line(r, [])[:4], fmt(r["score"])] for r in fp["rows"]])

    out += ["## Top disagreements", ""]
    if not result["disagreements"]:
        out += ["Only one arm: nothing to compare.", ""]
    if meta["items_hidden"]:
        out += [withheld, ""]
    for d in result["disagreements"]:
        out += [
            f"### `{d['baseline']}` vs `{d['arm']}`",
            "",
            f"Rank correlation {fmt(d['spearman'])}; overlap of the two top-{d['top_k']} lists: {fmt(d['top_k_overlap'])}. The label is shown here, after the fact, to see who was right.",
            "",
        ]
        if meta["items_hidden"]:
            continue
        out += [f"Ranked much higher by `{d['baseline']}`:", ""]
        out += _table(["title", "source", f"{d['baseline']} rank", f"{d['arm']} rank", "label"], [[r["title"][:70], r["source"], r["baseline_rank"], r["arm_rank"], r["label"] or "unlabelled"] for r in d["baseline_higher"]])
        out += [f"Ranked much higher by `{d['arm']}`:", ""]
        out += _table(["title", "source", f"{d['baseline']} rank", f"{d['arm']} rank", "label"], [[r["title"][:70], r["source"], r["baseline_rank"], r["arm_rank"], r["label"] or "unlabelled"] for r in d["arm_higher"]])

    retest = result["labels"]["retest"]
    out += ["## Label reliability", ""]
    if retest["n"]:
        out += [
            f"{retest['n']} items were shown twice. Same label both times: {round(retest['agreement'] * retest['n'])} of {retest['n']} ({fmt(retest['agreement'] * 100, 0)}%). "
            f"Weighted kappa {fmt(retest['kappa'])}. Flipped between SHOW and SKIP: {retest['show_skip_flips']} of {retest['n']}.",
            "",
            "No ranker can agree with these labels more consistently than the labeller agreed with themself.",
            "",
        ]
    else:
        out += ["No item was labelled twice, so the consistency of the labels is unknown.", ""]

    out += ["## Provenance", ""]
    out += _table(["arm", "kind", "model", "version", "profile", "code", "created"], [
        [n, a["kind"], ", ".join(a["models"] or []), a["question_set"] or a["prompt_version"], a["profile_hash"] or "", f"{(a['code'] or {}).get('commit', '')}", (a["created_at"] or "")[:16]]
        for n, a in meta["arms"].items()
    ])
    return "\n".join(out)
