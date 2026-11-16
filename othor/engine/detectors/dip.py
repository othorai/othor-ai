# SPDX-License-Identifier: Apache-2.0
"""Hartigan's dip test of unimodality, written for Othor AI from the method in Hartigan &
Hartigan (1985), "The dip test of unimodality", Annals of Statistics 13(1). No third-party code:
the `diptest` package is GPL, which the image can't ship (licence policy, 5 Nov 2026).

The dip of the empirical CDF F_n is the sup-distance to the nearest unimodal CDF. Computation,
on the sorted sample: start with the whole range and D = 0; take the greatest convex minorant G
(lower hull of F's lower corners) and the least concave majorant L (upper hull of its upper
corners); d = max(L − G). If d ≤ D, stop. Otherwise the modal interval shrinks to the span
between the G vertex and the L vertex around the widest gap, and D takes the larger of itself
and the distances of F from G on the left part and from L on the right part. When the interval
can't shrink, D = d. The dip is D / 2. Ties are spread by one ulp so every point is distinct.

The p-value is the share of seeded uniform samples of the same size whose dip is at least as
large (Hartigan's recommended null), with the usual +1 correction.
"""

from __future__ import annotations

import numpy as np

NULL_SAMPLES = 999


def _lower_hull(x: np.ndarray, y: np.ndarray, a: int, b: int) -> list[int]:
    h: list[int] = []
    for i in range(a, b + 1):
        while len(h) >= 2:
            i0, i1 = h[-2], h[-1]
            if (y[i1] - y[i0]) * (x[i] - x[i0]) >= (y[i] - y[i0]) * (x[i1] - x[i0]):
                h.pop()  # i1 is on or above the chord i0 → i: not on the lower hull
            else:
                break
        h.append(i)
    return h


def _upper_hull(x: np.ndarray, y: np.ndarray, a: int, b: int) -> list[int]:
    h: list[int] = []
    for i in range(a, b + 1):
        while len(h) >= 2:
            i0, i1 = h[-2], h[-1]
            if (y[i1] - y[i0]) * (x[i] - x[i0]) <= (y[i] - y[i0]) * (x[i1] - x[i0]):
                h.pop()
            else:
                break
        h.append(i)
    return h


def _interp(x: np.ndarray, y: np.ndarray, hull: list[int], a: int, b: int) -> np.ndarray:
    xs = x[hull]
    ys = y[hull]
    out: np.ndarray = np.interp(x[a : b + 1], xs, ys)
    return out


def dip(sample: np.ndarray) -> float:
    x = np.sort(np.asarray(sample, dtype=float))
    n = len(x)
    if n < 2 or x[0] == x[-1]:
        return 0.0 if n < 2 else 1 / (2 * n)
    # spread ties: each repeat moves up by one ulp from the one before (order kept)
    for i in range(1, n):
        if x[i] <= x[i - 1]:
            x[i] = np.nextafter(x[i - 1], np.inf)
    lower = np.arange(n) / n  # F(x_i−)
    upper = (np.arange(n) + 1) / n  # F(x_i)
    a, b, big = 0, n - 1, 0.0
    while True:
        g = _lower_hull(x, lower, a, b)
        lc = _upper_hull(x, upper, a, b)
        gv = _interp(x, lower, g, a, b)
        lv = _interp(x, upper, lc, a, b)
        gap = lv - gv
        k = int(np.argmax(gap))
        d = float(gap[k])
        if d <= big:
            break
        i = a + k
        gset, lset = set(g), set(lc)
        if i in gset:
            a2 = i
            b2 = min(j for j in lc if j >= i)
        elif i in lset:
            b2 = i
            a2 = max(j for j in g if j <= i)
        else:  # between vertices of both: the vertices around it
            a2 = max(j for j in g if j <= i)
            b2 = min(j for j in lc if j >= i)
        if (a2, b2) == (a, b):
            big = d
            break
        left = upper[a:a2] - gv[: a2 - a] if a2 > a else np.array([0.0])
        right = lv[b2 - a + 1 :] - lower[b2 + 1 : b + 1] if b2 < b else np.array([0.0])
        big = max(big, float(np.max(left)), float(np.max(right)))
        a, b = a2, b2
    return big / 2


def p_value(
    sample: np.ndarray, rng: np.random.Generator, samples: int = NULL_SAMPLES
) -> tuple[float, float]:
    """(dip, p) against seeded uniform samples of the same size."""
    d = dip(sample)
    n = len(sample)
    ge = sum(1 for _ in range(samples) if dip(rng.uniform(size=n)) >= d)
    return d, (1 + ge) / (samples + 1)
