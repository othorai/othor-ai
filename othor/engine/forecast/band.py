# SPDX-License-Identifier: Apache-2.0
"""Ranges, not points (v0.19, M19.1): P10 / P50 / P90 from the chosen method's out-of-sample
errors, then calibrated on its own track record.

- σ is the root-mean-square of the backtest's one-step errors (what the method actually missed by),
  not the in-sample fit; the 80% band is P50 ∓ z·σ·k·√h, wider each period ahead.
- Calibration: k is the smallest factor in {1.0, 1.1, …, 3.0} with which at least `COVER` of the
  backtest's actuals would have fallen inside their band. A band is never narrowed (k ≥ 1).
"""

from __future__ import annotations

import math

import numpy as np

LEVEL = 0.8
COVER = 0.8  # at least 80% of the backtest's actuals inside the 80% band
K_GRID = tuple(round(1 + 0.1 * i, 1) for i in range(21))  # 1.0 … 3.0


def z() -> float:
    from scipy import stats

    return float(stats.norm.ppf(0.5 + LEVEL / 2))


def sigma(errors: tuple[float, ...] | list[float]) -> float:
    e = np.asarray(errors, dtype=float)
    return float(np.sqrt(np.mean(e**2))) if len(e) else 0.0


def calibrate(errors: tuple[float, ...] | list[float]) -> tuple[float, int, int]:
    """(k, inside, n): the smallest k with which `COVER` of the one-step errors fall inside
    z·σ·k, and how many did at that k."""
    e = np.abs(np.asarray(errors, dtype=float))
    n = len(e)
    if not n:
        return 1.0, 0, 0
    s, zz = sigma(errors), z()
    need = math.ceil(COVER * n)
    for k in K_GRID:
        inside = int((e <= zz * s * k + 1e-12).sum())
        if inside >= need:
            return k, inside, n
    k = K_GRID[-1]
    return k, int((e <= zz * s * k + 1e-12).sum()), n


def half_width(s: float, k: float, h: int) -> float:
    """Half the 80% band, `h` periods ahead (1 = next)."""
    return z() * s * k * math.sqrt(h)
