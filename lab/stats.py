"""Small statistics helpers for the results: intervals and the per-round retention fit."""

from __future__ import annotations

import math
import random


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Proportion k/n with its Wilson score interval: (p, lo, hi). n = 0 gives (nan, 0, 1)."""
    if n <= 0:
        return float("nan"), 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return p, max(0.0, mid - half), min(1.0, mid + half)


def mean_range(xs: list[float]) -> tuple[float, float, float]:
    """(mean, min, max); nan for an empty list."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return float("nan"), float("nan"), float("nan")
    return sum(xs) / len(xs), min(xs), max(xs)


def bootstrap_ci(xs: list[float], iters: int = 2000, seed: int = 0, level: float = 0.95) -> tuple[float, float, float]:
    """(mean, lo, hi): percentile bootstrap of the mean."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(sum(rng.choice(xs) for _ in range(n)) / n for _ in range(iters))
    a = (1 - level) / 2
    return sum(xs) / n, means[int(a * (iters - 1))], means[int((1 - a) * (iters - 1))]


def fit_retention(rounds_to_acc: dict[int, float], full: bool = False, floor: float = 1e-3):
    """Least-squares fit of log(acc_k) = log(a) + k*log(r): r is the share of what is left that survives each extra
    compaction round. Returns r, or (a, r) with full=True. Accuracies are clamped to `floor` before the log."""
    pts = [(k, math.log(max(floor, v))) for k, v in sorted(rounds_to_acc.items()) if v is not None]
    if len(pts) < 2:
        raise ValueError("need at least two rounds")
    n = len(pts)
    mx = sum(k for k, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((k - mx) ** 2 for k, _ in pts)
    slope = sum((k - mx) * (y - my) for k, y in pts) / sxx
    r, a = math.exp(slope), math.exp(my - slope * mx)
    return (a, r) if full else r
