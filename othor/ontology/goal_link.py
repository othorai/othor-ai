# SPDX-License-Identifier: Apache-2.0
"""The link between a suggestion and the goals it serves (#76, docs/plans/next-1126.md §3 step 4;
built so Business Model 2.3 Goal-aware suggestions reuses it, docs/plans/features.md).

Each goal's state is read from the run's own results, never a model: `at_risk` when the goal test
recorded it at risk (detection), `off_track` when tonight's projection falls short of the target
(the forecast step's goals), `on_track` when it doesn't, `unknown` without a projection. A
suggestion serves a goal when the goal's number is among the numbers it's built from, or sits in
the same area. Ranking a list of suggestions puts those that serve a goal that is off track (or
at risk) first, then those that serve any goal, then the list's own order: a stable sort, so the
same inputs give the same order.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# worst first: the order a suggestion's goal is picked in when it serves more than one
STATES = ("at_risk", "off_track", "unknown", "on_track")
BEHIND = frozenset({"at_risk", "off_track"})
PLAIN = {
    "at_risk": "at risk",
    "off_track": "off track",
    "unknown": "not yet projected",
    "on_track": "on track",
}


@dataclass(frozen=True)
class GoalState:
    key: str
    name: str
    metric: str
    state: str  # at_risk · off_track · on_track · unknown
    area: str | None = None

    @property
    def behind(self) -> bool:
        return self.state in BEHIND

    @property
    def plain(self) -> str:
        return PLAIN[self.state]


def states(
    spec: Mapping[str, Any],
    forecast_goals: Iterable[Mapping[str, Any]] = (),
    at_risk: Iterable[str] = (),
) -> list[GoalState]:
    """Every goal of the spec with its state tonight, in the spec's order (by key)."""
    metrics = spec.get("metrics") or {}
    risky = set(at_risk)
    projected = {str(g["key"]): g for g in forecast_goals if g.get("key")}
    out = []
    for g in sorted((spec.get("forecast") or {}).get("goals") or [], key=lambda g: g["key"]):
        key = str(g["key"])
        metric = str(g.get("metric") or "")
        if key in risky:
            state = "at_risk"
        elif key in projected:
            state = "on_track" if projected[key].get("on_track") else "off_track"
        else:
            state = "unknown"
        area = metrics.get(metric, {}).get("area")
        out.append(
            GoalState(key, str(g.get("name") or metric), metric, state, str(area) if area else None)
        )
    return out


def at_risk_keys(detection: Iterable[Mapping[str, Any]]) -> list[str]:
    """The goals the run's goal test recorded at risk (detection.json)."""
    from othor.engine.detect import GOAL_AT_RISK

    out = []
    for r in detection:
        sl = r.get("slots") or {}
        if sl.get("kind") == "goal" and r.get("reason") == GOAL_AT_RISK and sl.get("goal"):
            out.append(str(sl["goal"]))
    return sorted(set(out))


def served(
    goals: Sequence[GoalState], numbers: Iterable[str], area: str | None
) -> GoalState | None:
    """The goal a suggestion serves (its built-from numbers, else its area), the one most behind
    first; None when it serves none."""
    uses = set(numbers)
    hits = [g for g in goals if g.metric in uses]
    if not hits and area:
        hits = [g for g in goals if g.area and g.area == area]
    if not hits:
        return None
    return min(hits, key=lambda g: (STATES.index(g.state), g.key))


def rank[T](items: Sequence[T], goal_of: Callable[[T], GoalState | None]) -> list[T]:
    """Serving a goal that is behind first, then serving any goal, then the list's own order."""

    def key(ix: tuple[int, T]) -> tuple[int, int]:
        g = goal_of(ix[1])
        return (0 if g is not None and g.behind else 1 if g is not None else 2, ix[0])

    return [x for _, x in sorted(enumerate(items), key=key)]
