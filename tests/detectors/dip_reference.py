# SPDX-License-Identifier: Apache-2.0
"""An independent, slow reference for Hartigan's dip (tests only): the dip of the empirical CDF
F_n is the smallest d such that some unimodal CDF G stays within d of F_n. G is piecewise
linear with knots at the data points (F_n is constant between them); unimodal means its slopes
rise up to the modal segment and fall after it. For each modal segment m, the smallest d is a
linear programme; the dip is the smallest over m.

Constraints at each sorted distinct x_i (F jumps from (i−1)/n to i/n there; G is continuous):
  i/n − d ≤ g_i ≤ (i−1)/n + d,   g_1 ≤ d,   g_n ≥ 1 − d,   g non-decreasing,
  slopes s_k = (g_{k+1} − g_k)/(x_{k+1} − x_k): s_k ≤ s_{k+1} for k < m, s_k ≥ s_{k+1} for k ≥ m.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import linprog


def dip_lp(x: np.ndarray) -> float:
    x = np.sort(np.asarray(x, float))
    n = len(x)
    if n < 2 or x[0] == x[-1]:
        return 0.0
    assert len(np.unique(x)) == n, "distinct values only"
    best = 1.0
    nv = n + 1  # g_1..g_n, d
    dx = np.diff(x)
    for m in range(n - 1):
        a_ub, b_ub = [], []

        def row(coefs: dict[int, float], rhs: float, a_ub: list = a_ub, b_ub: list = b_ub) -> None:
            r = np.zeros(nv)
            for k, v in coefs.items():
                r[k] += v
            a_ub.append(r)
            b_ub.append(rhs)

        d = n
        for i in range(n):
            row({i: -1.0, d: -1.0}, -(i + 1) / n)  # g_i ≥ i/n − d
            row({i: 1.0, d: -1.0}, i / n)  # g_i ≤ (i−1)/n + d
        row({0: 1.0, d: -1.0}, 0.0)  # g_1 ≤ d
        row({n - 1: -1.0, d: -1.0}, -1.0)  # g_n ≥ 1 − d
        for k in range(n - 1):
            row({k: 1.0, k + 1: -1.0}, 0.0)  # non-decreasing
        for k in range(n - 2):
            # s_k = (g_{k+1} − g_k)/dx_k ; s_{k+1} = (g_{k+2} − g_{k+1})/dx_{k+1}
            # c·g = s_k − s_{k+1}
            c = {k: -1 / dx[k], k + 1: 1 / dx[k] + 1 / dx[k + 1], k + 2: -1 / dx[k + 1]}
            if k < m:  # rising up to the modal segment: s_k − s_{k+1} ≤ 0
                row(c, 0.0)
            else:  # falling after it: s_{k+1} − s_k ≤ 0
                row({i: -v for i, v in c.items()}, 0.0)
        cost = np.zeros(nv)
        cost[d] = 1.0
        res = linprog(
            cost, A_ub=np.array(a_ub), b_ub=np.array(b_ub), bounds=[(0, 1)] * nv, method="highs"
        )
        if res.status == 0:
            best = min(best, float(res.x[d]))
    return best
