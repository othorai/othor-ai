# SPDX-License-Identifier: Apache-2.0
"""Synthetic series for detector golden tests: a planted signal and its clean twin. Seeded."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import numpy as np

from othor.engine.detectors.base import Context, SeriesIndex
from othor.engine.store import Point

RUN = dt.date(2026, 11, 2)  # Monday; data to Sun 1 Nov
LAST_WEEK = dt.date(2026, 10, 26)


def weeks(n: int, last: dt.date = LAST_WEEK) -> list[dt.date]:
    return [last - dt.timedelta(weeks=n - 1 - i) for i in range(n)]


def days(n: int, last: dt.date = RUN - dt.timedelta(days=1)) -> list[dt.date]:
    return [last - dt.timedelta(days=n - 1 - i) for i in range(n)]


def points(
    metric: str,
    grain: str,
    periods: list[dt.date],
    values: Any,
    breakdown: str = "",
    value: str | None = None,
    reconstructed: bool = False,
) -> list[Point]:
    return [
        Point(metric, grain, breakdown, value, p, Decimal(str(round(float(v), 6))), reconstructed)
        for p, v in zip(periods, values, strict=True)
    ]


def noise(seed: int, n: int, sd: float = 1.0) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, sd, n)


def spec(**metrics: dict[str, Any]) -> dict[str, Any]:
    from othor.ontology.compiler.build import DETECTOR_DEFAULTS

    return {
        "metrics": {k: {"status": "active", "type": "base", **m} for k, m in metrics.items()},
        "order": sorted(metrics),
        "breakdowns": {
            "region": {"name": "Region"},
            "customer": {"name": "Customer", "entity": True},
        },
        "detectors": DETECTOR_DEFAULTS,
        "suppressions": [],
        "rules": [],
    }


def context(
    spec_doc: dict[str, Any], pts: list[Point], probes: dict[str, Any] | None = None
) -> Context:
    return Context(spec_doc, "spec-hash", RUN, SeriesIndex(pts), probes or {})
