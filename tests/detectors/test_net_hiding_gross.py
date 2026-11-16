# SPDX-License-Identifier: Apache-2.0
"""Net hiding gross: offsetting regional moves under a flat total fire; parts that move with
the total don't. Money: the largest fall."""

import datetime as dt

import numpy as np

from othor.engine.detectors.net_hiding_gross import run
from tests.detectors.synth import context, noise, points, spec

MONTHS = [dt.date(2024 + (m + 3) // 12, (m + 3) % 12 + 1, 1) for m in range(31)]  # Apr 24 → Oct 26


def _ctx(south, west, north):
    sp = spec(rev={"kind": "flow", "unit": "inr", "agg": "sum", "breakdowns": ["region"]})
    pts = (
        points("rev", "month", MONTHS, south, "region", "South")
        + points("rev", "month", MONTHS, west, "region", "West")
        + points("rev", "month", MONTHS, north, "region", "North")
    )
    return context(sp, pts)


def test_offsetting_moves_fire_with_the_fall_as_money():
    s = 28e6 + noise(1, 31, 1.2e6)
    w = 6.5e6 + noise(2, 31, 0.8e6)
    n = 4e6 + noise(3, 31, 0.3e6)
    s[-1] = s[-2] - 14e6
    w[-1] = w[-2] + 12.1e6
    (r,), cov = run(_ctx(s, w, n))
    assert float(r.p_value) < 1e-6 and r.strong and r.entities == ["South", "West"]
    assert abs(float(r.money) - 14e6) / 14e6 < 0.01
    assert r.slots["fall"]["name"] == "South" and cov.status == "ran"


def test_parts_moving_with_the_total_do_not_fire():
    for seed in range(10):
        s = 28e6 + noise(10 + seed, 31, 1.2e6)
        w = 6.5e6 + noise(20 + seed, 31, 0.8e6)
        n = 4e6 + noise(30 + seed, 31, 0.3e6)
        (r,), _ = run(_ctx(s, w, n))
        assert float(r.p_value) > 0.001, seed


def test_a_real_fall_everywhere_is_not_net_hiding_gross():
    s = 28e6 + noise(4, 31, 1.2e6)
    w = 6.5e6 + noise(5, 31, 0.8e6)
    n = 4e6 + noise(6, 31, 0.3e6)
    s[-1] -= 10e6
    w[-1] -= 3e6
    (r,), _ = run(_ctx(s, w, n))
    assert float(r.p_value) == 1 and r.reason == "the headline moved with its parts"
    assert np.isfinite(float(r.statistic))
