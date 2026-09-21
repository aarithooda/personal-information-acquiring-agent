"""Ranking and agreement metrics. Pure functions: no files, no models, no PIA imports.

Everything works on GRADES listed in the order a system ranked the items (best first):
    2 = SHOW    (I would want this surfaced)
    1 = MAYBE   (interesting, not clearly worth a headline)
    0 = SKIP

"Positive" comes in two strengths, and every headline number is reported for both:
    strict   SHOW only               (what earns a headline)
    lenient  SHOW and MAYBE          (what would be a good extra)

Why so many metrics? Each answers a different question (see benchmarks/README.md). The short version:
    precision@K / recall@K   what the reader would actually see in a briefing of size K
    capture@K                the same, but relative to the best any system could do at that K
    nDCG@K                   like precision, but rewards putting SHOW above MAYBE and finding both early
    AP, ROC-AUC, Spearman    how good the WHOLE ordering is, independent of any one K
"""

import math
import random
from collections.abc import Sequence

SHOW, MAYBE, SKIP = 2, 1, 0
DEFAULT_KS = (4, 10, 16, 32)


def effective_k(k: int, n_labelled: int, n_total: int) -> int:
    """K to use when only some items are labelled.

    The labelling order is a random shuffle, so the labelled items are a random sample of all items. In a random
    sample of a fraction f of the items, about f x K of the arm's top K land among the labelled ones, so evaluating
    the labelled sub-list at K x f estimates the full-data metric (with more noise). With every item labelled
    this returns K unchanged."""
    if n_labelled >= n_total:
        return k
    return max(1, round(k * n_labelled / n_total))


# ---------- single metrics ----------


def _average_precision(grades: Sequence[int], threshold: int) -> float | None:
    hits, total = 0, 0.0
    for rank, grade in enumerate(grades, start=1):
        if grade >= threshold:
            hits += 1
            total += hits / rank
    return total / hits if hits else None


def _dcg(gains: Sequence[int]) -> float:
    return sum(g / math.log2(rank + 1) for rank, g in enumerate(gains, start=1))


def _ndcg(grades: Sequence[int], cut: int) -> float | None:
    ideal = _dcg(sorted(grades, reverse=True)[:cut])
    return _dcg(grades[:cut]) / ideal if ideal > 0 else None


def _auc(grades: Sequence[int], negatives: set[int]) -> float | None:
    """Probability that a random SHOW item is ranked above a random negative one (0.5 = coin flip, 1 = perfect)."""
    positives_seen, wins, n_pos, n_neg = 0, 0, 0, 0
    for grade in grades:
        if grade == SHOW:
            positives_seen += 1
            n_pos += 1
        elif grade in negatives:
            wins += positives_seen
            n_neg += 1
    return wins / (n_pos * n_neg) if n_pos and n_neg else None


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1  # ties share the average of the ranks they span
        i = j + 1
    return ranks


def _spearman(grades: Sequence[int]) -> float | None:
    """Rank correlation between 'ranked higher by the system' and 'labelled higher by the human'."""
    n = len(grades)
    if n < 2:
        return None
    x = _average_ranks([float(n - i) for i in range(n)])  # position 0 is the best rank
    y = _average_ranks([float(g) for g in grades])
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def summarize(grades: Sequence[int], ks: Sequence[int] = DEFAULT_KS, n_total: int | None = None) -> dict[str, float | None]:
    """Every metric for one ranking. `n_total` (the number of items in the whole set) is only needed when `grades`
    covers a labelled subset; K is then scaled by `effective_k`. Keys keep the NOMINAL K, e.g. 'recall@16'."""
    out: dict[str, float | None] = {}
    n = len(grades)
    n_show = sum(g == SHOW for g in grades)
    n_lenient = sum(g >= MAYBE for g in grades)
    for k in ks:
        cut = min(effective_k(k, n, n_total) if n_total else k, n)
        top = grades[:cut]
        hits, hits_lenient = sum(g == SHOW for g in top), sum(g >= MAYBE for g in top)
        out[f"hits@{k}"] = hits
        out[f"precision@{k}"] = hits / cut if cut else None
        out[f"recall@{k}"] = hits / n_show if n_show else None
        out[f"capture@{k}"] = hits / min(cut, n_show) if n_show and cut else None
        out[f"precision_lenient@{k}"] = hits_lenient / cut if cut else None
        out[f"recall_lenient@{k}"] = hits_lenient / n_lenient if n_lenient else None
        out[f"ndcg@{k}"] = _ndcg(grades, cut) if cut else None
    out["ap"] = _average_precision(grades, SHOW)
    out["ap_lenient"] = _average_precision(grades, MAYBE)
    out["auc_show_vs_skip"] = _auc(grades, {SKIP})
    out["auc_show_vs_rest"] = _auc(grades, {SKIP, MAYBE})
    out["spearman"] = _spearman(grades)
    return out


# ---------- uncertainty ----------


def bootstrap_metrics(
    items: Sequence[str],
    grades: dict[str, int],
    positions: dict[str, dict[str, int]],
    metrics: Sequence[str],
    *,
    ks: Sequence[int] = DEFAULT_KS,
    n_total: int | None = None,
    n_boot: int = 1000,
    seed: int = 0,
) -> dict[str, dict[str, list[float | None]]]:
    """Resample the ITEMS with replacement and recompute the metrics, many times.

    The spread of those recomputed numbers is what sampling noise does to a result on a set this small.
    Every arm is scored on the SAME resamples, which is what makes differences between arms meaningful
    (a "paired" bootstrap): both arms suffer or enjoy the same unlucky draw of items.
    `positions[arm][item]` is the item's rank in that arm's ordering (lower = better)."""
    rng = random.Random(seed)
    out = {arm: {name: [] for name in metrics} for arm in positions}
    for _ in range(n_boot):
        sample = [items[rng.randrange(len(items))] for _ in items]
        for arm, pos in positions.items():
            ordered = sorted(sample, key=pos.__getitem__)  # duplicates of one item sit side by side
            summary = summarize([grades[i] for i in ordered], ks, n_total)
            for name in metrics:
                out[arm][name].append(summary[name])
    return out


def percentile_ci(samples: Sequence[float | None], alpha: float = 0.05) -> tuple[float | None, float | None]:
    """A (1 - alpha) percentile interval; undefined resamples are ignored. Rounds outward, so it is never too narrow."""
    values = sorted(v for v in samples if v is not None)
    if not values:
        return None, None
    last = len(values) - 1
    return values[math.floor(alpha / 2 * last)], values[math.ceil((1 - alpha / 2) * last)]


def paired_delta(a: Sequence[float | None], b: Sequence[float | None]) -> dict:
    """B minus A over paired resamples: how big, how uncertain, and how often B actually came out ahead."""
    deltas = [y - x for x, y in zip(a, b) if x is not None and y is not None]
    if not deltas:
        return {"n": 0, "mean": None, "ci": (None, None), "p_b_better": None, "p_tie": None}
    eps = 1e-12
    return {
        "n": len(deltas),
        "mean": sum(deltas) / len(deltas),
        "ci": percentile_ci(deltas),
        "p_b_better": sum(d > eps for d in deltas) / len(deltas),
        "p_tie": sum(abs(d) <= eps for d in deltas) / len(deltas),
    }


# ---------- how reliable are the human labels? (test-retest) ----------


def exact_agreement(pairs: Sequence[tuple[int, int]]) -> float | None:
    return sum(a == b for a, b in pairs) / len(pairs) if pairs else None


def weighted_kappa(pairs: Sequence[tuple[int, int]]) -> float | None:
    """Cohen's kappa with linear weights: agreement beyond what chance alone would give, where SHOW-vs-MAYBE is a
    smaller disagreement than SHOW-vs-SKIP. 1 = identical, 0 = no better than chance, below 0 = systematic disagreement."""
    if not pairs:
        return None
    weight = lambda a, b: 1 - abs(a - b) / 2  # noqa: E731  (agreement weight on the 0/1/2 scale)
    n = len(pairs)
    p_first = {g: sum(a == g for a, _ in pairs) / n for g in (SKIP, MAYBE, SHOW)}
    p_second = {g: sum(b == g for _, b in pairs) / n for g in (SKIP, MAYBE, SHOW)}
    observed = sum(weight(a, b) for a, b in pairs) / n
    expected = sum(p_first[i] * p_second[j] * weight(i, j) for i in p_first for j in p_second)
    return (observed - expected) / (1 - expected) if expected < 1 else None

