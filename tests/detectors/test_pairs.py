# SPDX-License-Identifier: Apache-2.0
"""Broken relationship and lead/lag on declared pairs: planted fire, clean twins don't, and
without a pair record each says it needs one."""

import numpy as np

from othor.engine.detectors.pairs import broken, lead_lag
from tests.detectors.synth import context, noise, points, spec, weeks

W = weeks(80)


def _ctx(a, b, relation):
    sp = spec(
        freight={"kind": "flow", "unit": "inr", "agg": "sum"},
        metres={"kind": "flow", "unit": "metres", "agg": "sum"},
    )
    sp["pairs"] = [{"key": "fm", "a": "freight", "b": "metres", "relation": relation}]
    return context(sp, points("freight", "week", W, a) + points("metres", "week", W, b))


def test_a_relationship_that_held_then_broke_fires_with_excess_money():
    m = 90_000 + noise(1, 80, 15_000)
    f = 4.2 * m + noise(2, 80, 15_000)
    f[-8:] = 4.2 * 90_000 * 1.19 + noise(3, 8, 20_000)  # freight decouples from metres
    (r,), cov = broken(_ctx(f, m, "together"))
    assert float(r.p_value) < 1e-3 and float(r.slots["r_before"]) > 0.85
    assert float(r.slots["r_after"]) < 0.5 and r.money is not None and float(r.money) > 0
    assert cov.status == "ran"


def test_a_relationship_that_holds_does_not_fire():
    for seed in range(10):
        m = 90_000 + noise(10 + seed, 80, 15_000)
        f = 4.2 * m + noise(20 + seed, 80, 15_000)
        (r,), _ = broken(_ctx(f, m, "together"))
        assert float(r.p_value) > 0.001, seed


def test_lead_lag_finds_the_lag_and_a_shuffled_twin_does_not():
    lead = 100 + noise(4, 80, 10)
    follow = np.roll(lead, 4) + noise(5, 80, 2)
    (r,), _ = lead_lag(_ctx(lead, follow, "leads"))
    assert r.slots["lag_weeks"] == 4 and float(r.p_value) <= 0.002 and r.money is None
    (r2,), _ = lead_lag(_ctx(100 + noise(6, 80, 10), 100 + noise(7, 80, 10), "leads"))
    assert float(r2.p_value) > 0.01


def test_without_a_pair_record_they_say_what_they_need():
    sp = spec(freight={"kind": "flow", "unit": "inr", "agg": "sum"})
    ctx = context(sp, [])
    for fn in (broken, lead_lag):
        results, cov = fn(ctx)
        assert results == [] and cov.status == "could_not_run" and cov.needs == "pair"
