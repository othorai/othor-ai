# SPDX-License-Identifier: Apache-2.0
"""When the forecast says nothing (v0.19, M19.1): the rows are still stored (Forecast breach and
the goals read them), but the series is marked with a plain reason, shown instead of a number.

- `short_history`: under 8 weeks of history (the 8-week rule, docs/04 §5);
- `source_stale`: the newest period is stale (its source wasn't updated: the M15.7c freshness);
- `too_wide`: the next period's 80% band is wider than `WIDE` × |P50|: too uncertain to call.
(`definition_changed` comes with M19.2, which compares a run with the one before it.)
"""

from __future__ import annotations

WIDE = {"week": 0.6, "day": 0.6, "month": 0.4, "quarter": 0.4}


def reason(
    *,
    weeks: int,
    band_weeks: int,
    stale_after_last: bool,
    grain: str,
    p50: float | None,
    lower: float | None,
    upper: float | None,
) -> str | None:
    if weeks < band_weeks:
        return "short_history"
    if stale_after_last:
        return "source_stale"
    if (
        p50 is not None
        and lower is not None
        and upper is not None
        and (p50 == 0 or (upper - lower) > WIDE.get(grain, 0.6) * abs(p50))
    ):
        return "too_wide"
    return None
