# SPDX-License-Identifier: Apache-2.0
"""Rising variance: daily spread that widens while the level holds fires, with the process's ₹
as money; a steady twin doesn't; a level that moved is left to the changepoint."""

import datetime as dt

import numpy as np

from othor.engine.detectors.rising_variance import run
from tests.detectors.synth import RUN, context, days, noise, points, spec

D = days(140)


def _ctx(x, rev=None):
    sp = spec(
        lead={
            "kind": "flow",
            "unit": "days",
            "agg": "avg",
            "breakdowns": ["plant"],
            "at_stake": {"metric": "rev"},
        },
        rev={"kind": "flow", "unit": "inr", "agg": "sum", "breakdowns": ["plant"]},
    )
    sp["breakdowns"]["plant"] = {"name": "Plant"}
    pts = points("lead", "day", D, x, "plant", "Coimbatore")
    if rev is not None:
        pts += points("rev", "day", D, rev, "plant", "Coimbatore")
    return context(sp, pts)


def test_widening_spread_fires_with_money_at_stake():
    x = 4.1 + noise(1, 140, 0.4)
    x[-50:] = 4.1 + noise(2, 50, 1.2)  # widens 50 days ago, mean holds
    rev = np.full(140, 200_000.0)
    (r,), _ = run(_ctx(x, rev))
    assert float(r.p_value) < 1e-6 and r.strong
    start = dt.date.fromisoformat(r.slots["started"])
    assert RUN - dt.timedelta(days=56) <= start <= RUN - dt.timedelta(days=42)
    expect = 200_000.0 * (RUN - start).days
    assert abs(float(r.money) - expect) / expect < 0.01


def test_steady_spread_does_not_fire():
    for seed in range(10):
        (r,), _ = run(_ctx(4.1 + noise(10 + seed, 140, 0.4)))
        assert float(r.p_value) > 0.001, seed


def test_a_level_that_moved_is_not_rising_variance():
    x = 4.1 + noise(3, 140, 0.4)
    x[-28:] = 6.0 + noise(4, 28, 1.2)
    (r,), _ = run(_ctx(x))
    assert float(r.p_value) == 1 and "level moved" in r.reason
