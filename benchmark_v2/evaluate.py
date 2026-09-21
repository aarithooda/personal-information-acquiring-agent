"""Benchmark v2 evaluation: run each arm once on the frozen corpus, then analyse EXACTLY as METHODOLOGY.md sections 6 to 14 specify.

This module was written AFTER the labels were frozen (METHODOLOGY section 5 gates this phase), implements only what the frozen document
defines, and is committed before any arm is run. It changes no corpus, label, methodology or production code.

Aggregate patterns only: the report names no item. The labels are treated as the frozen human signal, not as objective per-item truth,
so uncertainty (bootstrap intervals, a permutation test, the repeat-label reliability) carries the weight instead of any item-level story.

Implementation choices the methodology leaves open (stated in the report too): the permutation p-value is (1 + #{null >= observed}) / (1 + shuffles),
the conservative convention; the bootstrap resamples item indices with random.Random(seed); and the "popularity" reference ordering computes production's
within-source percentile over the ranking pool.
"""

import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import typer

from benchmark_v2 import common as c
from benchmark_v2 import freeze as fz
from benchmarks.arms import Arm, Ordering, _export, build_scratch_db, code_version, production_order, run_jev_arm, run_llm_arm, save_arm
from benchmarks.common import ARMS_DIR, FROZEN_LABELS_FILE, SCRATCH_DIR, BenchmarkError, read_json, write_json_atomic
from benchmarks.labelset import GRADE, LabelSet  # noqa: F401  (GRADE: label name -> grade, the mapping fixed in METHODOLOGY section 4)
from benchmarks.metrics import paired_delta, percentile_ci, summarize, weighted_kappa
from benchmarks.snapshot import Snapshot, is_title_only, load_snapshot

PRIMARY = {"P1": "ndcg@16", "P2": "ap_lenient", "P3": "auc_lenient"}
NAMES = {2: "SHOW", 1: "MAYBE", 0: "SKIP"}
MODEL_ARMS = ("jev-v2", "llm-stage1", "jev-v1")
REFERENCE_ARMS = ("R1-newest", "R2-text-first", "R3-popularity")
DECISION_COMPARATORS = ("llm-stage1", "R2-text-first")
MIN_POSITIVES_PER_GROUP = 5
D4_BOUNDS = (20, 140)
D1_ALPHA = 0.025
RR_FLOOR = 0.5


# ---------- metrics (METHODOLOGY section 8) ----------


def auc_lenient(grades) -> float | None:
    """P(a random SHOW-or-MAYBE item is ranked above a random SKIP item); grades are in rank order."""
    seen = wins = pos = neg = 0
    for g in grades:
        if g >= 1:
            seen += 1
            pos += 1
        else:
            wins += seen
            neg += 1
    return wins / (pos * neg) if pos and neg else None


def full_metrics(grades, ks=c.KS) -> dict[str, float | None]:
    m = summarize(grades, ks)
    m["auc_lenient"] = auc_lenient(grades)
    n_pos = sum(g >= 1 for g in grades)
    for k in ks:
        cut = min(k, len(grades))
        hits = sum(g >= 1 for g in grades[:cut])
        m[f"capture_lenient@{k}"] = hits / min(cut, n_pos) if n_pos and cut else None
    return m


def primary(grades) -> dict[str, float | None]:
    m = full_metrics(grades, ks=(16,))
    return {name: m[key] for name, key in PRIMARY.items()}


# ---------- the frozen inputs ----------


def _v2_labels(data_dir: Path) -> tuple[dict[int, int], str]:
    data = read_json(data_dir / FROZEN_LABELS_FILE)
    return {row["item_id"]: GRADE[row["label"]] for row in data["labels"]}, data["labels_hash"]


def load_v1_grades() -> dict[int, int]:
    """The ONE place v1 labels are read: for the repeat-label reliability analysis (METHODOLOGY section 10), never for construction or arms."""
    data = read_json(c.V1_ITEMS.parent / "labels_frozen.json")
    return {row["item_id"]: GRADE[row["label"]] for row in data["labels"]}


@dataclass
class Context:
    data_dir: Path
    snapshot: Snapshot
    manifest: dict
    grade: dict[int, int]
    labels_hash: str
    v1_grades: dict[int, int]
    items: list[dict] = field(default_factory=list)
    new_items: list[dict] = field(default_factory=list)

    @property
    def repeat_map(self) -> dict[int, int]:
        return {m["v2_id"]: m["v1_item_id"] for m in self.manifest["repeats"]}


def load_context(data_dir: Path, v1_grades: dict[int, int] | None = None) -> Context:
    snapshot = load_snapshot(data_dir / c.ITEMS_FILE)  # verifies the corpus hash
    manifest = read_json(data_dir / c.MANIFEST_FILE)
    grade, labels_hash = _v2_labels(data_dir)
    new_ids = {m["v2_id"] for m in manifest["new"]}
    return Context(
        data_dir, snapshot, manifest, grade, labels_hash,
        load_v1_grades() if v1_grades is None else v1_grades,
        items=snapshot.items, new_items=[i for i in snapshot.items if i["id"] in new_ids],
    )


def gate(data_dir: Path) -> None:
    """Evaluation may start only when the labels are frozen and the freeze is intact (methodology section 5 and 15)."""
    if not (data_dir / FROZEN_LABELS_FILE).is_file():
        raise BenchmarkError("the labels are not frozen yet: python -m benchmark_v2 freeze-labels")
    problems = fz.verify(data_dir, methodology_path=c.METHODOLOGY, expect_no_model_output=False)
    if problems:
        raise BenchmarkError("the frozen state no longer holds, so this evaluation would be void: " + "; ".join(problems))


# ---------- reference orderings (METHODOLOGY section 6) ----------


def reference_orderings(items: list[dict]) -> dict[str, Ordering]:
    from pia.briefing.rank import POPULARITY_METRICS, _metric

    recency = lambda i: i["published_at"] or i["discovered_at"]  # noqa: E731
    by_id = sorted(items, key=lambda i: i["id"])
    newest = sorted(by_id, key=recency, reverse=True)  # stable: ties keep id order
    text_first = sorted(newest, key=lambda i: is_title_only(i))  # False (has text) sorts first, newest first within each group

    population = {s: sorted(v for i in items if (v := _metric(i["signals"], s)) is not None) for s in POPULARITY_METRICS}

    def percentile(source: str, value: float) -> float:
        values = population[source]
        below = sum(1 for x in values if x < value)
        equal = sum(1 for x in values if x == value)
        return (below + 0.5 * equal) / len(values)

    def popularity(i: dict) -> float:
        return max((percentile(s, v) for s in POPULARITY_METRICS if (v := _metric(i["signals"], s)) is not None), default=0.0)

    popular = sorted(newest, key=popularity, reverse=True)  # stable on the newest-first order: ties by newest
    make = lambda seq: Ordering([i["id"] for i in seq], [i["id"] for i in seq], {}, [])  # noqa: E731
    return {"R1-newest": make(newest), "R2-text-first": make(text_first), "R3-popularity": make(popular)}


# ---------- uncertainty (METHODOLOGY section 9) ----------


def bootstrap_all(items, grade, positions, n: int, seed: int) -> dict[str, dict[str, list]]:
    """Resample the ITEMS with replacement; every arm is scored on the SAME resamples, so differences are paired."""
    rng = random.Random(seed)
    out: dict[str, dict[str, list]] = {arm: {} for arm in positions}
    for _ in range(n):
        sample = [items[rng.randrange(len(items))] for _ in items]
        for arm, pos in positions.items():
            for key, value in full_metrics([grade[i] for i in sorted(sample, key=pos.__getitem__)]).items():
                out[arm].setdefault(key, []).append(value)
    return out


def permutation_pvalues(items, grade, positions, n: int, seed: int) -> dict[str, dict[str, dict]]:
    """One-sided: how often do labels shuffled among the items (counts fixed) score at least as well as the real ones?
    p = (1 + #{shuffles >= observed}) / (1 + shuffles): never exactly 0."""
    rng = random.Random(seed)
    orders = {arm: sorted(items, key=pos.__getitem__) for arm, pos in positions.items()}
    observed = {arm: primary([grade[i] for i in order]) for arm, order in orders.items()}
    hits = {arm: {p: 0 for p in PRIMARY} for arm in orders}
    labels = [grade[i] for i in items]
    for _ in range(n):
        shuffled = labels[:]
        rng.shuffle(shuffled)
        g = dict(zip(items, shuffled))
        for arm, order in orders.items():
            m = primary([g[i] for i in order])
            for p in PRIMARY:
                if m[p] is not None and observed[arm][p] is not None and m[p] >= observed[arm][p] - 1e-12:
                    hits[arm][p] += 1
    return {arm: {p: {"observed": observed[arm][p], "p": None if observed[arm][p] is None else (1 + hits[arm][p]) / (n + 1)} for p in PRIMARY} for arm in orders}


# ---------- repeat-label reliability (METHODOLOGY section 10) ----------


def reliability(v2: dict[int, int], v1: dict[int, int], pairs: dict[int, int]) -> dict:
    ids = sorted(pairs)
    first = [v1[pairs[i]] for i in ids]  # the v1 label
    second = [v2[i] for i in ids]  # the v2 label
    confusion = {NAMES[a]: {NAMES[b]: sum(1 for x, y in zip(first, second) if x == a and y == b) for b in (0, 1, 2)} for a in (0, 1, 2)}
    kappa = weighted_kappa(list(zip(first, second)))
    pos = [(s, f) for s, f in zip(second, first) if s >= 1]
    neg = [(s, f) for s, f in zip(second, first) if s == 0]
    wins = sum((fp > fn) + 0.5 * (fp == fn) for _, fp in pos for _, fn in neg)
    return {
        "n": len(ids),
        "exact_agreement": sum(a == b for a, b in zip(first, second)) / len(ids) if ids else None,
        "weighted_kappa": kappa,
        "kappa_below_floor": kappa is not None and kappa < c.KAPPA_FLOOR,
        "confusion": confusion,  # rows: the v1 label, columns: the v2 label
        "show_skip_flips": sum(1 for a, b in zip(first, second) if {a, b} == {0, 2}),
        "positive_rate_v1": sum(a >= 1 for a in first) / len(ids) if ids else None,
        "positive_rate_v2": sum(b >= 1 for b in second) / len(ids) if ids else None,
        "noise_reference_auc_lenient": wins / (len(pos) * len(neg)) if pos and neg else None,
    }


# ---------- title-only (section 11) and strata (section 12) ----------


def _order(ids, pos):
    return sorted(ids, key=pos.__getitem__)


def title_only_analysis(ids, grade, positions, title_only_ids) -> dict:
    to = [i for i in ids if i in title_only_ids]
    tx = [i for i in ids if i not in title_only_ids]
    positives = [i for i in ids if grade[i] >= 1]
    pos_share = sum(i in title_only_ids for i in positives) / len(positives) if positives else None
    out = {
        "n": {"title_only": len(to), "has_text": len(tx)},
        "positive_rate": {"title_only": sum(grade[i] >= 1 for i in to) / len(to) if to else None, "has_text": sum(grade[i] >= 1 for i in tx) / len(tx) if tx else None},
        "corpus_title_only_share": len(to) / len(ids),
        "positives_title_only_share": pos_share,
        "arms": {},
    }
    for arm, pos in positions.items():
        order = _order(ids, pos)
        share = lambda k: sum(i in title_only_ids for i in order[:k]) / min(k, len(order))  # noqa: E731
        s32 = share(32)
        rr = s32 / pos_share if pos_share else None
        out["arms"][arm] = {
            "top16_title_only_share": share(16),
            "top32_title_only_share": s32,
            "representation_ratio": rr,
            "under_represented": rr is not None and rr < RR_FLOOR,
            "auc_lenient_title_only": auc_lenient([grade[i] for i in _order(to, pos)]),
            "auc_lenient_has_text": auc_lenient([grade[i] for i in _order(tx, pos)]),
        }
    return out


def strata(ids, grade, positions, dimensions: dict[str, dict[int, str]]) -> dict:
    out: dict = {}
    for dim, label_of in dimensions.items():
        groups: dict[str, list[int]] = {}
        for i in ids:
            groups.setdefault(label_of[i], []).append(i)
        out[dim] = {}
        for name, members in sorted(groups.items()):
            positives = [i for i in members if grade[i] >= 1]
            cell = {
                "n": len(members), "show": sum(grade[i] == 2 for i in members), "maybe": sum(grade[i] == 1 for i in members), "skip": sum(grade[i] == 0 for i in members),
                "positive_rate": len(positives) / len(members), "too_few_positives": len(positives) < MIN_POSITIVES_PER_GROUP, "arms": {},
            }
            for arm, pos in positions.items():
                g = [grade[i] for i in _order(members, pos)]
                cell["arms"][arm] = {
                    "auc_lenient": auc_lenient(g),
                    "ap_lenient": full_metrics(g, ks=(1,))["ap_lenient"],
                    "positives_in_top32": sum(pos[i] < 32 for i in positives) / len(positives) if positives else None,
                }
            out[dim][name] = cell
    return out


# ---------- decision rules (METHODOLOGY section 14) ----------


def verdicts(perm: dict, comparisons: dict, kappa: float | None, n_positive: int) -> dict:
    low, high = D4_BOUNDS
    underpowered = n_positive < low or n_positive > high
    noisy = kappa is not None and kappa < c.KAPPA_FLOOR

    def wrap(text: str) -> str:
        if underpowered:
            return f"Underpowered ({n_positive} SHOW+MAYBE among the primary items; the bounds are {low} to {high}): no verdict issued"
        return f"Limited by label noise: {text}" if noisy else text

    p2, p3 = perm["P2"]["p"], perm["P3"]["p"]
    d1 = "Ranking skill demonstrated" if p2 is not None and p3 is not None and p2 < D1_ALPHA and p3 < D1_ALPHA else "Ranking skill not demonstrated"
    d2 = {}
    for name, cmp_ in comparisons.items():
        ap_lo, ap_hi = cmp_["P2"]["ci"]
        ndcg = cmp_["P1"]["mean"]
        if ap_lo is not None and ndcg is not None and ap_lo > 0 and ndcg > 0:
            text = f"A outperforms {name}"
        elif ap_hi is not None and ndcg is not None and ap_hi < 0 and ndcg < 0:
            text = f"A underperforms {name}"
        else:
            text = "cannot tell"
        d2[name] = wrap(text)
    return {"D1": wrap(d1), "D2": d2, "D3": {"weighted_kappa": kappa, "applies": noisy}, "D4": {"n_positive": n_positive, "underpowered": underpowered}}


# ---------- provenance and arms (METHODOLOGY section 6) ----------


def ranking_version() -> str:
    """A hash of the production ranking code (rank_items and the curator's filter) that orders every arm."""
    import pia.briefing.curate as curate
    import pia.briefing.rank as rank

    text = b"".join(Path(m.__file__).read_bytes().replace(b"\r\n", b"\n") for m in (rank, curate))
    return hashlib.sha256(text).hexdigest()


def _provenance(arm: Arm, **extra) -> Arm:
    arm.meta.update({"benchmark_version": c.BENCHMARK_VERSION, "ranking_version": ranking_version(), "arm": arm.name, **extra})
    return arm


def run_jev_v2(ctx: Context, profile, client, scratch_dir: Path, *, model: str, now: datetime, workers: int = 4, code=code_version) -> Arm:
    """The production Jev layer (current design), run once on every corpus item."""
    from pia.jev.design import CURRENT_DESIGN
    from pia.jev.triage import JevTriager

    started = time.monotonic()
    conn = build_scratch_db(ctx.snapshot, scratch_dir / "jev-v2.db")
    try:
        report = JevTriager(client, profile, workers=workers, design=CURRENT_DESIGN).triage_pending(conn, now)
        arm = _export(conn, ctx.snapshot, name="jev-v2", kind="jev", now=now, code=code(), started=started, report=report)
    finally:
        conn.close()
    return _provenance(arm, question_set=CURRENT_DESIGN.version, derivation=CURRENT_DESIGN.version, jev_model_requested=model)


def run_llm(ctx: Context, llm, scratch_dir: Path, *, now: datetime, code=code_version) -> Arm:
    return _provenance(run_llm_arm(ctx.snapshot, llm, scratch_dir, name="llm-stage1", now=now, code=code))


def run_jev_v1(ctx: Context, profile, client, scratch_dir: Path, *, model: str, now: datetime, workers: int = 4, code=code_version) -> Arm:
    return _provenance(run_jev_arm(ctx.snapshot, profile, client, scratch_dir, name="jev-v1", now=now, workers=workers, code=code), jev_model_requested=model, derivation="jev-triage-v1")


def save_once(arm: Arm, data_dir: Path) -> Path:
    if not arm.meta["complete"]:
        raise BenchmarkError(
            f"{arm.name}: only {arm.meta['n_scored']} of {arm.meta['n_items']} items were scored, so nothing was saved. Scored items are kept; run the same command again to "
            "complete it (identical code, configuration and inputs; METHODOLOGY section 6)."
        )
    return save_arm(arm, data_dir)


# ---------- the analysis ----------


def _counts(ids, grade) -> dict:
    return {name: sum(grade[i] == g for i in ids) for g, name in ((2, "SHOW"), (1, "MAYBE"), (0, "SKIP"))}


def analyze(ctx: Context, arms: dict[str, Arm], *, now: datetime, code=code_version, resamples: int = c.BOOTSTRAP_RESAMPLES, shuffles: int = c.PERMUTATION_SHUFFLES) -> dict:
    from benchmark_v2.cli import native_category

    for needed in ("jev-v2", "llm-stage1"):
        if needed not in arms:
            raise BenchmarkError(f"the evaluation needs the {needed!r} arm (METHODOLOGY section 6); run it first: python -m benchmark_v2.evaluate arm {needed}")
    pool = ctx.new_items
    ids = [i["id"] for i in pool]
    pool_snapshot = Snapshot(pool, ctx.snapshot.snapshot_id, ctx.snapshot.exported_at)

    orders = {name: production_order(pool_snapshot, arm) for name, arm in arms.items()}
    orders.update(reference_orderings(pool))
    positions = {name: o.positions() for name, o in orders.items()}
    grades = {name: [ctx.grade[i] for i in sorted(ids, key=pos.__getitem__)] for name, pos in positions.items()}

    metrics = {name: full_metrics(g) for name, g in grades.items()}
    samples = bootstrap_all(ids, ctx.grade, positions, resamples, c.BOOTSTRAP_SEED)
    ci = lambda name, key: list(percentile_ci(samples[name][key]))  # noqa: E731
    primary_out = {n: {p: {"value": metrics[n][k], "ci": ci(n, k)} for p, k in PRIMARY.items()} for n in positions}
    perm = permutation_pvalues(ids, ctx.grade, positions, shuffles, c.PERMUTATION_SEED)

    comparisons = {}
    for other in positions:
        if other == "jev-v2":
            continue
        comparisons[other] = {}
        for p, key in PRIMARY.items():
            d = paired_delta(samples[other][key], samples["jev-v2"][key])  # jev-v2 minus the comparator; "b better" = jev-v2 ahead
            comparisons[other][p] = {"mean": d["mean"], "ci": list(d["ci"]), "share_a_ahead": d["p_b_better"], "share_tied": d["p_tie"], "n": d["n"]}

    repeat = reliability(ctx.grade, ctx.v1_grades, ctx.repeat_map)
    n_positive = sum(ctx.grade[i] >= 1 for i in ids)
    title_only_ids = {i["id"] for i in pool if is_title_only(i)}
    by_id = {i["id"]: i for i in pool}
    dims = {
        "source": {i: by_id[i]["source"] for i in ids},
        "native_category": {i: native_category(by_id[i]) for i in ids},
        "title_only": {i: "title-only" if i in title_only_ids else "has text" for i in ids},
    }
    for name in ("jev-v2", "llm-stage1"):
        dims[f"model_category:{name}"] = {i: (arms[name].records.get(i) or {}).get("category", "unscored") for i in ids}

    all_orders = {name: production_order(ctx.snapshot, arm) for name, arm in arms.items()}
    all_orders.update(reference_orderings(ctx.items))
    all_items = {n: primary([ctx.grade[i] for i in o.ranked]) for n, o in all_orders.items()}
    model_only = {n: primary([ctx.grade[i] for i in sorted(ids, key=orders[n].model_only_positions().__getitem__)]) for n in arms}

    return {
        "meta": {
            "benchmark_version": c.BENCHMARK_VERSION, "generated_at": now.isoformat(), "code": code(), "corpus_id": ctx.snapshot.snapshot_id, "labels_hash": ctx.labels_hash,
            "methodology_sha256": fz.sha256_text_file(c.METHODOLOGY), "primary_pool": len(ids), "n_items": len(ctx.items), "bootstrap_resamples": resamples, "bootstrap_seed": c.BOOTSTRAP_SEED,
            "permutation_shuffles": shuffles, "permutation_seed": c.PERMUTATION_SEED, "ks": list(c.KS), "arms": {n: a.meta for n, a in arms.items()},
        },
        "labels": {"new": _counts(ids, ctx.grade), "all": _counts([i["id"] for i in ctx.items], ctx.grade), "positives_new": n_positive},
        "primary": primary_out,
        "permutation": perm,
        "comparisons": comparisons,
        "reliability": repeat,
        "title_only": title_only_analysis(ids, ctx.grade, positions, title_only_ids),
        "strata": strata(ids, ctx.grade, positions, dims),
        "secondary": metrics,
        "model_only": model_only,
        "all_items": all_items,
        "verdicts": verdicts(perm["jev-v2"], {n: comparisons[n] for n in DECISION_COMPARATORS}, repeat["weighted_kappa"], n_positive),
    }


# ---------- the report ----------


def fmt(x, places=3) -> str:
    return "n/a" if x is None else f"{x:.{places}f}"


def _table(headers, rows) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|", *["| " + " | ".join(str(x) for x in r) + " |" for r in rows], ""]


def _cell(entry) -> str:
    lo, hi = entry["ci"]
    return fmt(entry["value"]) if lo is None else f"{fmt(entry['value'])} [{fmt(lo)}, {fmt(hi)}]"


def render(r: dict) -> str:
    m, out = r["meta"], []
    out += [f"# Benchmark {m['benchmark_version']} evaluation report", "", f"Frozen methodology `{m['methodology_sha256'][:12]}` · corpus `{m['corpus_id']}` ({m['n_items']} items) · labels `{m['labels_hash']}` · generated {m['generated_at']} at code `{m['code']['commit']}`", "",
            "Aggregate patterns only. The labels are the frozen human signal, not objective per-item truth; no item is named or explained here.", ""]
    arms = list(r["primary"])
    lab = r["labels"]
    out += ["## Labels", "", f"Primary set (160 new items): {lab['new']} · SHOW+MAYBE = {lab['positives_new']}. All 200: {lab['all']}.", ""]
    out += ["## Primary metrics", "", f"On the {m['primary_pool']} new items, each arm ranked among them by PIA's production ranking (references by their own rule). 95% percentile bootstrap intervals ({m['bootstrap_resamples']} resamples, seed {m['bootstrap_seed']}).", ""]
    out += _table(["arm", "P1 nDCG@16", "P2 AP lenient", "P3 AUC lenient"], [[a, *[_cell(r["primary"][a][p]) for p in PRIMARY]] for a in arms])
    out += ["## Above-chance test", "", f"One-sided permutation test, {m['permutation_shuffles']} shuffles, seed {m['permutation_seed']}; p = (1 + shuffles at least as good) / (1 + shuffles). D1 needs both P2 and P3 below {D1_ALPHA}.", ""]
    out += _table(["arm", "P1 p", "P2 p", "P3 p"], [[a, *[fmt(r["permutation"][a][p]["p"], 4) for p in PRIMARY]] for a in arms])
    out += ["## Comparisons", "", "Paired bootstrap difference, `jev-v2` minus the comparator, on the same resamples. **Decision comparators: llm-stage1 and R2-text-first**; the rest are descriptive.", ""]
    rows = [[f"{n}{' (decision)' if n in DECISION_COMPARATORS else ''}", *[f"{fmt(c_[p]['mean'])} [{fmt(c_[p]['ci'][0])}, {fmt(c_[p]['ci'][1])}] · ahead {fmt(c_[p]['share_a_ahead'], 2)}" for p in PRIMARY]] for n, c_ in r["comparisons"].items()]
    out += _table(["comparator", "P1 nDCG@16", "P2 AP lenient", "P3 AUC lenient"], rows)
    rel = r["reliability"]
    out += ["## Repeat-label reliability", "", f"{rel['n']} v1 items re-labelled in v2 (short interval, possible memory; **not** independent test items). Exact agreement {fmt(rel['exact_agreement'], 2)} · weighted kappa {fmt(rel['weighted_kappa'], 2)} (floor {c.KAPPA_FLOOR}) · SHOW/SKIP flips {rel['show_skip_flips']} · SHOW+MAYBE rate v1 {fmt(rel['positive_rate_v1'], 2)} to v2 {fmt(rel['positive_rate_v2'], 2)} · noise reference (v1 label as a ranker of the v2 label) AUC lenient {fmt(rel['noise_reference_auc_lenient'], 2)}.", ""]
    out += _table(["v1 label \\ v2 label", "SHOW", "MAYBE", "SKIP"], [[k, v["SHOW"], v["MAYBE"], v["SKIP"]] for k, v in rel["confusion"].items()])
    t = r["title_only"]
    out += ["## Title-only analysis", "", f"Title-only {t['n']['title_only']}, has text {t['n']['has_text']}. Positive rate: title-only {fmt(t['positive_rate']['title_only'], 2)}, has text {fmt(t['positive_rate']['has_text'], 2)}. Title-only share of the corpus {fmt(t['corpus_title_only_share'], 2)}, of the positives {fmt(t['positives_title_only_share'], 2)}. RR = title-only share of an arm's top 32 / title-only share of positives; flag if RR < {RR_FLOOR}.", ""]
    out += _table(["arm", "top-16 title-only share", "top-32 share", "RR", "flag", "AUC title-only", "AUC has text"], [[a, fmt(v["top16_title_only_share"], 2), fmt(v["top32_title_only_share"], 2), fmt(v["representation_ratio"], 2), "UNDER-REPRESENTED" if v["under_represented"] else "", fmt(v["auc_lenient_title_only"], 2), fmt(v["auc_lenient_has_text"], 2)] for a, v in t["arms"].items()])
    out += ["## Stratification", "", f"Within-stratum AUC / AP (lenient) per arm, and the share of the stratum's positives inside the arm's global top 32. Strata with fewer than {MIN_POSITIVES_PER_GROUP} SHOW+MAYBE are marked (few) and support no inference.", ""]
    focus = [a for a in ("jev-v2", "llm-stage1", "R2-text-first") if a in arms]
    for dim, cells in r["strata"].items():
        out += [f"### {dim}", ""]
        out += _table(["group", "n", "S/M/K", "pos rate", *[f"{a}: AUC · top32" for a in focus]], [[f"{g}{' (few)' if v['too_few_positives'] else ''}", v["n"], f"{v['show']}/{v['maybe']}/{v['skip']}", fmt(v["positive_rate"], 2), *[f"{fmt(v['arms'][a]['auc_lenient'], 2)} · {fmt(v['arms'][a]['positives_in_top32'], 2)}" for a in focus]] for g, v in cells.items()])
    ks = m["ks"]
    out += ["## Secondary metrics (descriptive only)", ""]
    keys = [f"ndcg@{k}" for k in ks] + [f"precision_lenient@{k}" for k in ks] + [f"recall_lenient@{k}" for k in ks] + [f"capture_lenient@{k}" for k in ks] + ["ap", "auc_show_vs_skip", "spearman"] + [f"recall@{k}" for k in ks] + [f"precision@{k}" for k in ks]
    out += _table(["metric", *arms], [[k, *[fmt(r["secondary"][a][k]) for a in arms]] for k in keys])
    out += ["Model-only order (model score alone, no popularity or cross-source boost), primary metrics:", ""]
    out += _table(["arm", "P1", "P2", "P3"], [[a, *[fmt(v[p]) for p in PRIMARY]] for a, v in r["model_only"].items()])
    out += ["All 200 items ranked together (secondary; includes the 40 repeats), primary metrics:", ""]
    out += _table(["arm", "P1", "P2", "P3"], [[a, *[fmt(v[p]) for p in PRIMARY]] for a, v in r["all_items"].items()])
    v = r["verdicts"]
    out += ["## Verdicts (METHODOLOGY section 14)", "", f"- **D1 above chance:** {v['D1']}"]
    out += [f"- **D2 versus {n}:** {text}" for n, text in v["D2"].items()]
    out += [f"- **D3 label noise:** weighted kappa {fmt(v['D3']['weighted_kappa'], 2)}; applies: {v['D3']['applies']}", f"- **D4 prevalence:** {v['D4']['n_positive']} SHOW+MAYBE among the primary items; underpowered: {v['D4']['underpowered']}", ""]
    out += ["## Provenance", ""]
    rows = []
    for name, meta in m["arms"].items():
        rows.append([name, meta.get("kind"), ", ".join(meta.get("models") or []), meta.get("question_set") or meta.get("prompt_version"), meta.get("jev_model_requested", ""), meta.get("profile_hash", ""), meta.get("derivation", ""), (meta.get("ranking_version") or "")[:12], (meta.get("code") or {}).get("commit", ""), str(meta.get("input_tokens", "")), meta.get("elapsed_seconds", ""), (meta.get("created_at") or "")[:16]])
    out += _table(["arm", "kind", "model id(s)", "question set / prompt", "jev requested", "profile", "derivation", "ranking", "commit", "input tokens", "seconds (last leg)", "created"], rows)
    return "\n".join(out)


# ---------- command line ----------

app = typer.Typer(add_completion=False, help="Benchmark v2 evaluation: run the arms once, then analyse exactly as METHODOLOGY.md specifies.")


def _friendly(fn):
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except BenchmarkError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(1)

    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


@app.command("arm")
@_friendly
def arm_command(which: str = typer.Argument(..., help="jev-v2 | llm-stage1 | jev-v1"), data_dir: Path = typer.Option(c.DATA_DIR, "--data-dir")) -> None:
    """Run ONE arm once on the frozen corpus and freeze its raw output. Prints aggregates only."""
    import httpx

    from pia.config import DEFAULT_PROFILE, get_groq_api_key, get_jev_api_key
    from pia.http import USER_AGENT
    from pia.jev.client import JevClient
    from pia.llm.client import GroqClient
    from pia.profile import load_profile

    if which not in MODEL_ARMS:
        raise typer.BadParameter(f"must be one of {', '.join(MODEL_ARMS)}")
    gate(data_dir)
    if (data_dir / ARMS_DIR / f"{which}.json").exists():
        raise BenchmarkError(f"{which} already exists: every arm is run once and frozen (METHODOLOGY section 6)")
    ctx = load_context(data_dir, v1_grades={})
    now = datetime.now(timezone.utc)
    scratch = data_dir / SCRATCH_DIR
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True) as http:
        if which == "llm-stage1":
            arm = run_llm(ctx, GroqClient(get_groq_api_key(), http), scratch, now=now)
        else:
            profile = load_profile(DEFAULT_PROFILE)
            models = JevClient(get_jev_api_key(), http).list_models()  # sends no item data
            model = "jev-1.13.0" if "jev-1.13.0" in models else "jev-latest"
            if model != "jev-1.13.0":
                typer.echo("jev-1.13.0 is not available; using jev-latest and recording the version that answers (METHODOLOGY section 6).")
            client = JevClient(get_jev_api_key(), http, model=model)
            arm = (run_jev_v2 if which == "jev-v2" else run_jev_v1)(ctx, profile, client, scratch, model=model, now=now)
    path = save_once(arm, data_dir)
    m = arm.meta
    typer.echo(f"{which}: {m['n_scored']} of {m['n_items']} items scored in {m['elapsed_seconds']} s; models {m['models']}; input tokens {m.get('input_tokens', 'n/a')}; failed items {m['report']['failed_items']}. Saved {path}.")


@app.command("analyze")
@_friendly
def analyze_command(data_dir: Path = typer.Option(c.DATA_DIR, "--data-dir")) -> None:
    """Analyse exactly as METHODOLOGY sections 7 to 14 specify, and write the report."""
    from benchmarks.arms import load_arm

    gate(data_dir)
    ctx = load_context(data_dir)
    names = [*MODEL_ARMS]
    arms = {n: load_arm(data_dir / ARMS_DIR / f"{n}.json") for n in names if (data_dir / ARMS_DIR / f"{n}.json").is_file()}
    result = analyze(ctx, arms, now=datetime.now(timezone.utc))
    out = data_dir / "reports"
    out.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out / "benchmark_v2_report.json", result)
    text = render(result)
    (out / "benchmark_v2_report.md").write_text(text, encoding="utf-8")
    typer.echo(text)
    typer.echo(f"\nSaved {out / 'benchmark_v2_report.md'} and .json")


if __name__ == "__main__":
    app()
