# SPDX-License-Identifier: Apache-2.0
"""Watch this (5d): the 90-day replay reads the stored series only and finds the first day the
indicator would have breached; the spread indicator is p90 − p10 over its window; a watch is
drafted from a rising-variance finding with a threshold between before and now."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import numpy as np

from othor.engine import watches
from othor.engine.detectors.base import SeriesIndex
from tests.detectors.synth import RUN, days, points

END = RUN - dt.timedelta(days=1)


def _series():
    d = days(200, END)
    r = np.random.default_rng(3)
    v = 4.1 + r.normal(0, 0.3, 200)
    v[150:] = 4.1 + r.normal(0, 1.4, 50)  # spread widens 50 days before the end
    return SeriesIndex(points("dispatch_time", "day", d, v, "plant", "Coimbatore")), d[150]


IND = {
    "kind": "spread",
    "metric": "dispatch_time",
    "grain": "day",
    "breakdown": "plant",
    "breakdown_value": "Coimbatore",
    "window_days": 7,
}


def test_the_replay_finds_the_first_breach_from_stored_series():
    s, widened = _series()
    out = watches.replay(IND, s, "2.0", "above", END)
    assert len(out["points"]) == 90
    first = dt.date.fromisoformat(out["first_breach"])
    assert widened <= first <= widened + dt.timedelta(days=10)
    # the value on a day equals the indicator computed on the series up to that day
    _day, v = out["points"][-1]
    assert v == watches.indicator(IND, s, [])
    assert Decimal(v) > 2


def test_a_watch_is_drafted_from_a_rising_variance_finding():
    s, widened = _series()
    f = {
        "detector": "rising_variance",
        "metric": "dispatch_time",
        "breakdown": "plant",
        "breakdown_value": "Coimbatore",
        "slots": {"started": widened.isoformat()},
        "state": "assessed",
    }
    d = watches.draft(f, s, {"metrics": {"dispatch_time": {"name": "Dispatch time"}}})
    assert d["indicator"] == IND and d["direction"] == "above"
    prior, now = Decimal(d["hint"]["value"]), Decimal(watches.indicator(IND, s, []))
    assert prior < Decimal(d["threshold"]) < now
    assert d["label"] == "Dispatch time spread, Coimbatore · p90 − p10 days, 7-day window"


def test_the_percentile_is_linear_interpolation_written_out():
    """Golden values (Hyndman & Fan type 7), so replay never depends on a numpy version."""
    from othor.engine.detectors.common import percentile

    assert percentile([1, 2, 3, 4], 0.9) == 3.7
    assert abs(percentile([1, 2, 3, 4], 0.1) - 1.3) < 1e-12
    assert percentile([5], 0.9) == 5.0
    assert percentile([4.0, 1.0, 9.0, 2.0, 7.0], 0.5) == 4.0
    r = np.random.default_rng(11).normal(4, 1, 97)
    for q in (0.1, 0.25, 0.9):
        assert abs(percentile(r, q) - float(np.quantile(r, q, method="linear"))) < 1e-12


def test_the_spread_indicator_golden_value():
    vals = [4.0, 4.2, 3.9, 6.1, 2.8, 5.5, 4.4]  # the 7-day window
    d = [END - dt.timedelta(days=6 - i) for i in range(7)]
    s = SeriesIndex(points("dispatch_time", "day", d, vals, "plant", "Coimbatore"))
    # p90 = 5.5 + 0.4·(6.1 − 5.5) = 5.74; p10 = 2.8 + 0.6·(3.9 − 2.8) = 3.46
    assert watches.indicator(IND, s, []) == "2.280000"


def test_drafted_thresholds_are_rounded_for_their_unit():
    from othor.engine.watches import draft_round

    assert draft_round(Decimal("4.2667"), "days") == Decimal("4.3")
    assert draft_round(Decimal("6379958.61"), "inr") == Decimal("6400000")
    assert draft_round(Decimal("0.0427"), "inr") == Decimal("0.043")
    assert draft_round(Decimal("13.6"), "count") == Decimal("14")
