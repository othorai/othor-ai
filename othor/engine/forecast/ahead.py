# SPDX-License-Identifier: Apache-2.0
"""What's known ahead (v0.19, M19.2): confirmed known patterns whose window touches a forecast
period, with their effect measured on this series' own past, not assumed.

- A pattern touches a period by the same rule as the mask (`store._mask`): a window covers only
  periods no longer than itself.
- The effect is the median ratio of the actual to the masked baseline (the line between its
  unmasked neighbours) over the periods the pattern touched before. It's applied (to P50 and the
  band) only with at least `MIN_OCCURRENCES` past windows; otherwise the pattern is named with
  "effect not yet measured".
Pure: no model, no clock.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import numpy as np

from othor.engine.detect import windows
from othor.engine.forecast.ets import masked, next_periods

MIN_OCCURRENCES = 2


def _touches(d: dt.date, grain: str, a: dt.date, b: dt.date) -> bool:
    end = next_periods(d, grain, 1)[0] - dt.timedelta(days=1)
    return a <= end and d <= b and (end - d) <= (b - a)


def _spans(p: dict[str, Any], years: range) -> list[tuple[dt.date, dt.date]]:
    settle = dt.timedelta(days=int(p.get("settle_days") or 0))
    return [(a, b + settle) for a, b in windows(p, years)]


def known_ahead(
    spec: dict[str, Any],
    metric: str,
    grain: str,
    periods: list[dt.date],
    values: list[float],
    future: list[dt.date],
) -> dict[str, list[dict[str, Any]]]:
    """period (iso) → the patterns touching it, each with its measured effect (or None)."""
    if not periods or not future:
        return {}
    years = range(periods[0].year - 1, future[-1].year + 2)
    y = np.asarray(values, dtype=float)
    out: dict[str, list[dict[str, Any]]] = {}
    for p in spec.get("suppressions", []):
        if p.get("confirmed", True) is False:
            continue
        ms = p.get("metrics") or []
        if ms and metric not in ms:
            continue
        spans = _spans(p, years)
        hits = [d for d in future if any(_touches(d, grain, a, b) for a, b in spans)]
        if not hits:
            continue
        mask = np.array([any(_touches(d, grain, a, b) for a, b in spans) for d in periods])
        base = masked(y, mask)
        past_windows = {(a, b) for a, b in spans if any(_touches(d, grain, a, b) for d in periods)}
        ratios = [
            float(y[i] / base[i])
            for i in range(len(y))
            if mask[i] and base[i] > 0 and np.isfinite(y[i])
        ]
        effect = None
        if len(past_windows) >= MIN_OCCURRENCES and ratios:
            effect = str(Decimal(repr(float(np.median(ratios)))).quantize(Decimal("0.0001")))
        entry = {
            "key": p.get("key"),
            "name": p.get("name") or p.get("key"),
            "effect": effect,
            "occurrences": len(past_windows),
        }
        for d in hits:
            out.setdefault(d.isoformat(), []).append(entry)
    return {k: sorted(v, key=lambda e: str(e["key"])) for k, v in sorted(out.items())}
