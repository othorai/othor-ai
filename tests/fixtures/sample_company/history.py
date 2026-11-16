# SPDX-License-Identifier: Apache-2.0
"""Earlier nightly snapshots for the steady-state fixture (P2 vs P15, decided 5 Nov 2026).

In the 1 Nov steady-state fixture Othor AI has been running for a while, so snapshot metrics have
real daily snapshots from before the run, taken by earlier nightly runs (`reconstructed=false`).
In the first-days fixture (first run Mon 2 Nov) nothing exists before the run and the same weeks
come only from the back-fill (`reconstructed=true`). Same fact, different evidence, different
grade: P2 is Measured, P15 is Assessed, and only the state and the provenance line differ.

`prior_snapshots` is the generator's own as-of truth for those earlier nights, computed from the
rendered receivables the way the engine's as-of SQL does (`measures._open_receivables`).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pandas as pd

from othor.engine.store import Point, dec
from tests.fixtures.sample_company import measures
from tests.fixtures.sample_company.generate import SampleCompany

STEADY_RUN = dt.date(2026, 11, 1)  # docs/11: the run of Sun 1 Nov
FIRST_DAYS_RUN = dt.date(2026, 11, 2)  # docs/11: the first run is on 2 Nov
SNAPSHOT_DAYS = 104 * 7  # as far back as the back-fill could reach


def _values(c: SampleCompany, day: dt.date) -> dict[str, Decimal]:
    o = measures._open_receivables(c, pd.Timestamp(day))
    age = (pd.Timestamp(day) - pd.to_datetime(o["due_date"])).dt.days
    return {
        "receivables_open": Decimal(str(round(float(o["open_amount"].sum()), 2))),
        "receivables_over_60": Decimal(str(round(float(o.loc[age > 60, "open_amount"].sum()), 2))),
    }


def prior_snapshots(
    c: SampleCompany,
    run_date: dt.date = STEADY_RUN,
    *,
    days: int = SNAPSHOT_DAYS,
    every: int = 1,
) -> list[Point]:
    """Daily (or every `every` days) as-of points before `run_date`, as earlier nightly runs
    would have stored them: totals of open receivables and receivables over 60 days."""
    out: list[Point] = []
    last = run_date - dt.timedelta(days=2)  # the run itself takes as of run_date − 1
    for i in range(0, days, every):
        day = last - dt.timedelta(days=i)
        for metric, v in _values(c, day).items():
            out.append(Point(metric, "day", "", None, day, dec(v), False))
    return out
