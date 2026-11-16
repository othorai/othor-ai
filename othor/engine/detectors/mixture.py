# SPDX-License-Identifier: Apache-2.0
"""A two-component Gaussian mixture by EM, against one component, chosen by BIC. Written for Othor
AI in numpy; deterministic (start from the 25th/75th percentiles, fixed iterations, no RNG)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ITERATIONS = 200
TOL = 1e-10


@dataclass(frozen=True)
class Fit:
    k: int
    weights: tuple[float, ...]
    means: tuple[float, ...]
    sds: tuple[float, ...]
    bic: float


def _logpdf(x: np.ndarray, m: float, s: float) -> np.ndarray:
    out: np.ndarray = -0.5 * np.log(2 * np.pi * s * s) - (x - m) ** 2 / (2 * s * s)
    return out


def one(x: np.ndarray) -> Fit:
    m, s = float(np.mean(x)), max(float(np.std(x)), 1e-9)
    ll = float(np.sum(_logpdf(x, m, s)))
    return Fit(1, (1.0,), (m,), (s,), -2 * ll + 2 * np.log(len(x)))


def two(x: np.ndarray) -> Fit:
    n = len(x)
    m = np.array([np.quantile(x, 0.25), np.quantile(x, 0.75)], dtype=float)
    s = np.array([max(float(np.std(x)), 1e-9)] * 2)
    w = np.array([0.5, 0.5])
    prev = -np.inf
    ll = prev
    for _ in range(ITERATIONS):
        lp = np.stack([np.log(w[j]) + _logpdf(x, m[j], s[j]) for j in range(2)])
        top = lp.max(axis=0)
        r = np.exp(lp - top)
        tot = r.sum(axis=0)
        ll = float(np.sum(top + np.log(tot)))
        r /= tot
        nk = r.sum(axis=1) + 1e-12
        w = nk / n
        m = (r @ x) / nk
        s = np.sqrt(np.maximum((r * (x[None, :] - m[:, None]) ** 2).sum(axis=1) / nk, 1e-12))
        if ll - prev < TOL:
            break
        prev = ll
    order = np.argsort(m)
    return Fit(
        2,
        tuple(float(v) for v in w[order]),
        tuple(float(v) for v in m[order]),
        tuple(float(v) for v in s[order]),
        -2 * ll + 5 * np.log(n),
    )


def best(x: np.ndarray) -> tuple[Fit, Fit]:
    """(the chosen fit, the other)."""
    a, b = one(x), two(x)
    return (b, a) if b.bic < a.bic else (a, b)
