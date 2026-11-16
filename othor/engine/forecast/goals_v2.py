# SPDX-License-Identifier: Apache-2.0
"""The chance of hitting a goal (v0.19, M19.2), from the v2 forecast's distribution.

- A value goal (DSO under 45 by 31 Mar): the v2 forecast for the goal's month, read as a normal
  distribution with mean P50 and sd = (P90 - P10) / 2.563 (the 80% band is 2.563 sd wide).
- A fiscal-year total (₹60 Cr in FY27): the actuals so far plus the v2 monthly P50s to the goal
  date; the remaining months' sds are added (they're taken to move together: the cautious choice,
  never overconfident).
- Rounded to a whole percent, kept between 1% and 99%: never "certain".
Pure: no model, no clock.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

WIDTH_SD = 2.563  # the 80% band (P10 to P90) is 2 × 1.2816 standard deviations wide


def _pct(p: float) -> int:
    return int(min(99, max(1, round(p * 100))))


def probability(mean: float, sd: float, target: float, direction: str) -> int:
    """P(the outcome lands on the goal's side of the target), in whole percent, 1-99."""
    from scipy import stats

    if sd <= 0:
        hit = mean >= target if direction == "at_least" else mean <= target
        return 99 if hit else 1
    below = float(stats.norm.cdf((target - mean) / sd))
    return _pct(1 - below if direction == "at_least" else below)


def _d(v: float) -> str:
    return str(Decimal(repr(float(v))).quantize(Decimal("0.01")))


def goals(
    spec: dict[str, Any],
    actuals: dict[str, list[tuple[dt.date, float]]],
    v2: dict[str, list[dict[str, Any]]],
    abstain: dict[str, str],
) -> list[dict[str, Any]]:
    """One entry per goal: its probability and projection, or why there is none. `actuals` and
    `v2` are by metric, at the month grain (v2 entries: period, p10, p50, p90)."""
    out = []
    for g in spec.get("forecast", {}).get("goals", []):
        metric = g.get("metric")
        by = dt.date.fromisoformat(str(g["by"]))
        target = float(Decimal(str(g["target"])))
        direction = g.get("direction", "at_least")
        base = {"key": g["key"], "metric": metric, "target": str(g["target"]), "by": g["by"]}
        why = abstain.get(f"{metric}|month")
        rows = {r["period"]: r for r in v2.get(metric, [])}
        if metric not in v2:  # forecast weekly only: no monthly forecast reaches the goal date
            out.append({**base, "probability": None, "why": "no_monthly_forecast"})
            continue
        if why:
            out.append({**base, "probability": None, "why": why})
            continue
        if g.get("aggregate") == "fiscal_year_total":
            start = by.replace(year=by.year - 1) + dt.timedelta(days=1)
            done = [(p, v) for p, v in actuals.get(metric, []) if start <= p <= by]
            to_date = sum(v for _, v in done)
            last = max((p for p, _ in done), default=None)
            ahead = [
                r
                for k, r in sorted(rows.items())
                if (last is None or k > last.isoformat()) and k <= by.isoformat()
            ]
            if not ahead or any(r["p10"] is None for r in ahead):
                out.append({**base, "probability": None, "why": "no_band"})
                continue
            mean = to_date + sum(float(r["p50"]) for r in ahead)
            sd = sum((float(r["p90"]) - float(r["p10"])) / WIDTH_SD for r in ahead)
            p10, p90 = mean - 1.2816 * sd, mean + 1.2816 * sd
        else:
            r = rows.get(by.replace(day=1).isoformat())
            if r is None or r["p10"] is None:
                out.append({**base, "probability": None, "why": "no_band"})
                continue
            mean, to_date = float(r["p50"]), None
            p10, p90 = float(r["p10"]), float(r["p90"])
            sd = (p90 - p10) / WIDTH_SD
        short = target - mean if direction == "at_least" else mean - target
        out.append(
            {
                **base,
                "direction": direction,
                "probability": probability(mean, sd, target, direction),
                "projection": _d(mean),
                "p10": _d(p10),
                "p90": _d(p90),
                "to_date": None if to_date is None else _d(to_date),
                "short_by": _d(short),
            }
        )
    return out
