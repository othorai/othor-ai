# SPDX-License-Identifier: Apache-2.0
"""A small Sweep world whose results depend on the kept detector versions (#138): a share with no
₹ of its own steps up and is linked to sales (changepoint 1: no money; 2: sales in full; 3: the
share of it the step says), and a plant's dispatch time widens 30 days back while its level holds
(rising variance 1: the start is the search's first week; 2: the week it widened).

Self-contained (numpy and the engine's Point only), so the code of an older commit can import it
by path and write what it made: `python make.py <out.json>` with that commit's code first on the
path. The JSON files beside it were made that way; `tests/unit/engine/test_old_sweeps_replay.py`
replays them with today's code."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import numpy as np

RUN = dt.date(2026, 11, 2)
WEEKS = [dt.date(2026, 10, 26) - dt.timedelta(weeks=51 - i) for i in range(52)]
DAYS = [RUN - dt.timedelta(days=140 - i) for i in range(140)]


def _noise(seed: int, n: int, sd: float) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, sd, n)


def spec() -> dict[str, Any]:
    from othor.ontology.compiler.build import DETECTOR_DEFAULTS

    metrics = {
        "away": {
            "name": "Scans away",
            "kind": "flow",
            "unit": "percent",
            "agg": "avg",
            "at_stake": {"metric": "sales"},
        },
        "sales": {"name": "Sales", "kind": "flow", "unit": "inr", "agg": "sum"},
        "lead": {
            "name": "Dispatch time",
            "kind": "flow",
            "unit": "days",
            "agg": "avg",
            "breakdowns": ["plant"],
            "at_stake": {"metric": "rev"},
        },
        "rev": {"name": "Line revenue", "kind": "flow", "unit": "inr", "agg": "sum"},
    }
    return {
        "metrics": {k: {"status": "active", "type": "base", **m} for k, m in metrics.items()},
        "order": sorted(metrics),
        "breakdowns": {"plant": {"name": "Plant"}},
        "detectors": DETECTOR_DEFAULTS,
        "suppressions": [],
        "rules": [],
    }


def points() -> list[Any]:
    from othor.engine.store import Point

    def pts(metric, grain, periods, values, breakdown="", value=None):
        return [
            Point(metric, grain, breakdown, value, p, Decimal(str(round(float(v), 6))), False)
            for p, v in zip(periods, values, strict=True)
        ]

    away = 0.032 + _noise(11, 52, 0.0004)
    away[-8:] += 0.006
    lead = 4.1 + _noise(1, 140, 0.4)
    lead[-30:] = 4.1 + _noise(2, 30, 1.2)
    return (
        pts("away", "week", WEEKS, away)
        + pts("sales", "week", WEEKS, np.full(52, 60_000_000.0))
        + pts("lead", "day", DAYS, lead, "plant", "Coimbatore")
        + pts("rev", "day", DAYS, np.full(140, 200_000.0), "plant", "Coimbatore")
    )


def sweep(context: dict[str, Any]) -> dict[str, Any]:
    from othor.engine.sweep import run_sweep

    out = run_sweep(spec(), "old-sweeps", RUN, [], points(), {}, context)
    keep = ("changepoint", "rising_variance")
    return {
        "results": [r for r in out.results if r["detector"] in keep],
        "findings": [f for f in out.findings if f["detector"] in keep],
        "results_hash": out.doc["results_hash"],
        "findings_hash": out.doc["findings_hash"],
    }
