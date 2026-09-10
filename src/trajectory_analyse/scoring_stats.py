#!/usr/bin/env python3
"""Pure-stdlib statistics for trajectory-score vs resolved evaluation.

The project intentionally uses only the Python standard library (see the
root ``pyproject.toml``: ``dependencies = []``, ``requires-python = ">=3.10"`),
and numpy/scipy/sklearn are not installed in the target interpreters. Everything
here therefore uses only ``math``/``statistics``/``random`` and follows standard
references.

Conventions
-----------
- Binary labels are ``0``/``1`` ints.
- All test functions return a two-sided p-value (or ``1.0`` on degenerate input).
- ``*_ci`` helpers use cluster (instance) bootstrap so repeated measurements of
  the same instance do not inflate the effective N.
"""

from __future__ import annotations

import math
import random
import statistics as st
from typing import Callable, Sequence


def normal_cdf(x: float) -> float:
    """Standard-normal cumulative distribution function."""
    return st.NormalDist().cdf(x)


def normal_sf(x: float) -> float:
    """1 - normal_cdf(x), numerically stable in the right tail."""
    return 1.0 - normal_cdf(x)


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def population_std(xs: Sequence[float]) -> float:
    return st.pstdev(xs) if len(xs) > 1 else 0.0


def _rankdata(values: Sequence[float]) -> list[float]:
    """Average ranks (1-based); ties share the mean rank."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j + 2) / 2.0  # ranks are 1-based: i+1..j+1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _tie_correction(sorted_vals: Sequence[float]) -> float:
    """Sum of (t**3 - t) over tie groups, for a sorted value list."""
    n = len(sorted_vals)
    if n < 2:
        return 0.0
    tie_sum = 0.0
    g = 1
    for i in range(1, n):
        if sorted_vals[i] == sorted_vals[i - 1]:
            g += 1
        else:
            if g > 1:
                tie_sum += g**3 - g
            g = 1
    if g > 1:
        tie_sum += g**3 - g
    return tie_sum


def mann_whitney_u(x: Sequence[float], y: Sequence[float]) -> dict:
    """Mann-Whitney U (relative to ``x``) with normal-approx two-sided p.

    Ties are handled via average ranks and the standard tie-corrected variance.
    """
    nx, ny = len(x), len(y)
    if nx == 0 or ny == 0:
        return {"U": float("nan"), "z": 0.0, "p": 1.0, "effect": float("nan")}
    combined = list(x) + list(y)
    ranks = _rankdata(combined)
    R_x = sum(ranks[:nx])
    U = R_x - nx * (nx + 1) / 2.0
    n = nx + ny
    tie_sum = _tie_correction(sorted(combined))
    mu = nx * ny / 2.0
    if n > 1:
        var = nx * ny * (n + 1) / 12.0 - nx * ny * tie_sum / (12.0 * n * (n - 1))
    else:
        var = 0.0
    if var <= 0:
        return {"U": U, "z": 0.0, "p": 1.0, "effect": 0.0}
    z = (abs(U - mu) - 0.5) / math.sqrt(var)
    p = 2.0 * normal_sf(z)
    # rank-biserial effect size: 2U/(nx*ny) - 1
    effect = 2.0 * U / (nx * ny) - 1.0
    return {"U": U, "z": z, "p": p, "effect": effect}


def auc_roc(y_true: Sequence[int], scores: Sequence[float]) -> float:
    """AUC-ROC via the Mann-Whitney relation (handles ties)."""
    pos = [s for s, y in zip(scores, y_true) if int(y) == 1]
    neg = [s for s, y in zip(scores, y_true) if int(y) == 0]
    if not pos or not neg:
        return float("nan")
    mw = mann_whitney_u(pos, neg)
    return mw["U"] / (len(pos) * len(neg))


def pr_auc(y_true: Sequence[int], scores: Sequence[float]) -> float:
    """Average precision (area under the precision-recall curve)."""
    pairs = sorted(zip(scores, [int(v) for v in y_true]), key=lambda t: -t[0])
    n_pos = sum(y for _, y in pairs)
    if n_pos == 0:
        return float("nan")
    tp = fp = 0
    ap = 0.0
    prev_recall = 0.0
    for _score, y in pairs:
        if y == 1:
            tp += 1
        else:
            fp += 1
        precision = tp / (tp + fp)
        recall = tp / n_pos
        ap += precision * (recall - prev_recall)
        prev_recall = recall
    return ap


def point_correlation(binary: Sequence[int], cont: Sequence[float]) -> float:
    """Point-biserial correlation between a binary and a continuous variable.

    Equals Pearson r for (0/1, cont). Returns 0.0 for a single group / zero sd.
    """
    if len(binary) < 2:
        return float("nan")
    g1 = [c for b, c in zip(binary, cont) if int(b) == 1]
    g0 = [c for b, c in zip(binary, cont) if int(b) == 0]
    if not g1 or not g0:
        return 0.0  # undefined -> treat as no relationship
    n1 = len(g1)
    n0 = len(g0)
    n = n1 + n0
    p = n1 / n
    sd = st.pstdev(cont)
    if sd == 0:
        return 0.0
    # Pearson r for (binary x, cont y): cov = p1*p0*(M1-M0); s_x = sqrt(p1*p0)
    return (mean(g1) - mean(g0)) * math.sqrt(p * (1.0 - p)) / sd


def sign_test(deltas: Sequence[float]) -> dict:
    """Two-sided sign test on signed deltas (zeros ignored, p=0.5)."""
    pos = sum(1 for d in deltas if d > 0)
    neg = sum(1 for d in deltas if d < 0)
    zeros = sum(1 for d in deltas if d == 0)
    n = pos + neg
    if n == 0:
        return {"pos": pos, "neg": neg, "zeros": zeros, "n": 0, "p": 1.0}
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    p = min(1.0, 2.0 * tail)
    return {"pos": pos, "neg": neg, "zeros": zeros, "n": n, "p": p}


def wilcoxon_signed_rank(deltas: Sequence[float]) -> dict:
    """Wilcoxon signed-rank on deltas vs zero (normal approx, tie corrected)."""
    d = [x for x in deltas if x != 0]
    n = len(d)
    if n < 1:
        return {"W_plus": 0.0, "z": 0.0, "p": 1.0, "n": 0}
    abs_d = [abs(x) for x in d]
    ranks = _rankdata(abs_d)
    tie_sum = _tie_correction(sorted(abs_d))
    W_plus = sum(r for x, r in zip(d, ranks) if x > 0)
    mu = n * (n + 1) / 4.0
    var = n * (n + 1) * (2 * n + 1) / 24.0 - tie_sum / 48.0
    if var <= 0:
        return {"W_plus": float(W_plus), "z": 0.0, "p": 1.0, "n": n}
    z = (abs(W_plus - mu) - 0.5) / math.sqrt(var)
    p = 2.0 * normal_sf(z)
    return {"W_plus": float(W_plus), "z": z, "p": p, "n": n}


def cluster_bootstrap_ci(
    cluster_ids: Sequence,
    rows: Sequence,
    compute: Callable[[list], float],
    *,
    n_boot: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Cluster-bootstrap percentile CI (resample whole clusters with replacement).

    ``rows[i]`` belongs to ``cluster_ids[i]``. ``compute(rows_subset) -> float`` is
    re-evaluated on each bootstrap resample. Returns ``(point_estimate, lo, hi)``.
    """
    if not rows:
        return (float("nan"), float("nan"), float("nan"))
    rng = random.Random(seed)
    unique = list(dict.fromkeys(cluster_ids))
    by_cluster: dict[object, list] = {}
    for cid, row in zip(cluster_ids, rows):
        by_cluster.setdefault(cid, []).append(row)
    try:
        point = compute(list(rows))
    except Exception:
        point = float("nan")
    boots: list[float] = []
    for _ in range(n_boot):
        sampled = [rng.choice(unique) for _ in unique]
        subset = []
        for cid in sampled:
            subset.extend(by_cluster[cid])
        if not subset:
            continue
        try:
            stat = compute(subset)
        except Exception:
            continue
        if isinstance(stat, float) and (math.isnan(stat) or math.isinf(stat)):
            continue
        boots.append(stat)
    if len(boots) < max(20, n_boot // 10):
        return (point, float("nan"), float("nan"))
    boots.sort()
    lo = boots[max(0, int(alpha / 2 * len(boots)))]
    hi = boots[min(len(boots) - 1, int((1 - alpha / 2) * len(boots)) - 1)]
    return (point, lo, hi)


def icc_2_1(judge1: Sequence[float], judge2: Sequence[float]) -> dict:
    """ICC(2,1): two-way random, single-measures absolute-agreement ICC.

    ``judge1``/``judge2`` are two judges' scores over the same subjects.
    """
    n = len(judge1)
    k = 2
    if n < 2 or len(judge2) != n:
        return {"icc": float("nan"), "pearson": float("nan")}
    s1 = sum(judge1)
    s2 = sum(judge2)
    gm = (s1 + s2) / (2.0 * n)
    subj_means = [(judge1[i] + judge2[i]) / 2.0 for i in range(n)]
    ss_subj = 2.0 * sum((m - gm) ** 2 for m in subj_means)
    ms_subj = ss_subj / (n - 1) if n > 1 else 0.0
    j1m = s1 / n
    j2m = s2 / n
    ss_judge = n * ((j1m - gm) ** 2 + (j2m - gm) ** 2)
    ms_judge = ss_judge / (k - 1)
    ss_res = 0.0
    for i in range(n):
        ss_res += (judge1[i] - subj_means[i] - j1m + gm) ** 2
        ss_res += (judge2[i] - subj_means[i] - j2m + gm) ** 2
    ms_res = ss_res / ((n - 1) * (k - 1)) if n > 1 else 0.0
    num = ms_subj - ms_res
    den = ms_subj + (k - 1) * ms_res + k * (ms_judge - ms_res) / n
    icc = num / den if den else float("nan")
    try:
        pear = st.correlation(judge1, judge2)
    except Exception:
        pear = float("nan")
    return {"icc": icc, "pearson": pear, "ms_subj": ms_subj, "ms_res": ms_res, "ms_judge": ms_judge}
