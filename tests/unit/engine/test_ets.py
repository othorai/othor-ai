# SPDX-License-Identifier: Apache-2.0
"""ETS forecasts (docs/04 §5, M7): the choice among a fixed candidate set is recorded, seasonality
only with two full cycles, no band before 8 weeks, masked windows left out of the fit, and the
same series gives the same rows to the byte (also in a fresh process)."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys

import numpy as np

from othor.engine.forecast.ets import fit, masked, next_periods


def _weekly(n=40, seed=1):
    r = np.random.default_rng(seed)
    p = [dt.date(2026, 1, 5) + dt.timedelta(weeks=i) for i in range(n)]
    return p, list(1e7 + r.normal(0, 3e5, n) + np.arange(n) * 2e4)


def test_the_same_series_gives_the_same_rows():
    p, v = _weekly()
    a, b = fit(p, v, "week", 1), fit(p, v, "week", 1)
    assert (
        a == b
        and a.method.startswith("ETS(A,")
        and a.points[0].period == p[-1] + dt.timedelta(weeks=1)
    )
    code = (
        "import datetime as dt, json, numpy as np\n"
        "from othor.engine.forecast.ets import fit\n"
        "r=np.random.default_rng(1); n=40\n"
        "p=[dt.date(2026,1,5)+dt.timedelta(weeks=i) for i in range(n)]\n"
        "v=list(1e7+r.normal(0,3e5,n)+np.arange(n)*2e4)\n"
        "f=fit(p,v,'week',1); x=f.points[0]\n"
        "print(json.dumps([f.method, f.aicc, x.point, x.lower]))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [a.method, a.aicc, a.points[0].point, a.points[0].lower]


def test_seasonality_only_with_two_cycles_and_the_band_rule():
    months = [dt.date(2023 + i // 12, i % 12 + 1, 1) for i in range(30)]
    vals = list(4e7 + 5e6 * np.sin(np.arange(30) / 12 * 2 * np.pi))
    assert fit(months, vals, "month", 3).method.endswith(",A)")
    assert fit(months[:20], vals[:20], "month", 3).method.endswith(",N)")
    f = fit(months, vals, "month", 3, band=False)
    assert all(x.lower is None and x.upper is None for x in f.points)
    assert [x.period for x in f.points] == next_periods(months[-1], "month", 3)
    assert fit(months[:5], vals[:5], "month", 1) is None  # too short


def test_masked_periods_are_left_out_of_the_fit():
    y = np.array([10.0, 11.0, 50.0, 13.0, 14.0])
    assert list(masked(y, np.array([False, False, True, False, False]))) == [10, 11, 12, 13, 14]
    p, v = _weekly()
    spiked = list(v)
    spiked[-3] *= 2  # a Diwali-like week
    mask = [False] * len(v)
    mask[-3] = True
    assert (
        fit(p, spiked, "week", 1, mask=mask).points
        == fit(p, [*v[:-3], (v[-4] + v[-2]) / 2, *v[-2:]], "week", 1).points
    )
