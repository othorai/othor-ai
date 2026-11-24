# SPDX-License-Identifier: Apache-2.0
"""The consequence line and its date (docs/04 §8): from the detector and the nearest relevant date
(month close, goal date, collection cycle). Pure; the brief ranks by the date.

- Net hiding gross, broken relationship, a level forecast breach: the close of the month now
  running ("November close in 29 days").
- Concentration: the next 60 days of revenue.
- A changepoint on a goal's metric, or on a metric the goal's metric is built from (receivables
  for DSO): the goal's date ("DSO under 45 days by 31 Mar").
- Otherwise the detector's own words, with no date (ranked as beyond 90 days).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

MONTH_CLOSE = ("net_hiding_gross", "broken_relationship", "forecast_breach")
GOAL_LINKED = ("changepoint",)  # a step that holds: what it endangers is the goal (4d: P2)
HORIZON = {"concentration": (60, "Next 60 days of revenue")}


def month_close(day: dt.date) -> dt.date:
    nxt = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return nxt - dt.timedelta(days=1)


def _goal(f: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    """The goal on this finding's metric or family; else a goal whose metric is built from a
    metric of this family (receivables → DSO). Earliest date first, then the key."""
    metrics = spec.get("metrics", {})
    mine = {f.get("metric"), f.get("family"), metrics.get(f.get("metric", ""), {}).get("family")}
    mine -= {None, ""}
    goals = sorted(spec.get("forecast", {}).get("goals", []), key=lambda g: (g["by"], g["key"]))
    for g in goals:
        if g["metric"] in mine or metrics.get(g["metric"], {}).get("family") in mine:
            return dict(g)
    for g in goals:
        parts = metrics.get(g["metric"], {}).get("components") or []
        if mine & {metrics.get(c, {}).get("family") or c for c in parts}:
            return dict(g)
    return None


def of(f: dict[str, Any], spec: dict[str, Any], run_date: dt.date) -> dict[str, Any]:
    det = f["detector"]
    if det in MONTH_CLOSE and (f.get("slots") or {}).get("kind") != "goal":
        d = month_close(run_date)
        days = (d - run_date).days
        # 0.33.1 (the 100-night run): "September close in 1 days"
        when = "today" if days == 0 else f"in {days} day" + ("" if days == 1 else "s")
        return {"text": f"{d:%B} close {when}", "date": d.isoformat()}
    if det in HORIZON:
        n, text = HORIZON[det]
        return {"text": text, "date": (run_date + dt.timedelta(days=n)).isoformat()}
    g = _goal(f, spec) if det in GOAL_LINKED else None
    if g is not None:
        return {"text": str(g.get("name") or g["key"]), "date": str(g["by"])}
    words = str((f.get("text") or {}).get("consequence") or "")
    return {"text": words[:1].upper() + words[1:], "date": None}
