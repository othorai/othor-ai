# SPDX-License-Identifier: Apache-2.0
"""Bimodal average and process artefact: planted shapes fire (and have no money: below
materiality until a target is set); clean twins don't; missing records → needs."""

import numpy as np

from othor.engine.detectors.dip import dip
from othor.engine.detectors.distributions import artefact, bimodal
from othor.engine.detectors.mixture import best
from tests.detectors.dip_reference import dip_lp
from tests.detectors.synth import context, spec


def _ctx(values):
    return context(
        spec(),
        [],
        {
            "distributions": [
                {"key": "aov", "metric": "average_order_value", "values": [str(v) for v in values]}
            ]
        },
    )


def test_two_groups_of_orders_fire_with_no_money():
    r = np.random.default_rng(1)
    v = np.concatenate(
        [r.lognormal(np.log(40_000), 0.3, 1500), r.lognormal(np.log(300_000), 0.3, 600)]
    )
    (res,), cov = bimodal(_ctx(v))
    assert float(res.p_value) <= 0.002 and res.money is None and len(res.slots["groups"]) == 2
    lo, hi = sorted(float(g["typical"]) for g in res.slots["groups"])
    assert 35_000 < lo < 46_000 and 260_000 < hi < 345_000 and cov.status == "ran"


def test_one_group_does_not_fire():
    for seed in range(5):
        v = np.random.default_rng(10 + seed).lognormal(np.log(80_000), 0.6, 2000)
        (res,), _ = bimodal(_ctx(v))
        assert float(res.p_value) > 0.01, seed


def test_dip_matches_the_exact_reference_and_known_values():
    rng = np.random.default_rng(7)
    for _ in range(60):
        x = np.unique(np.round(rng.normal(size=int(rng.integers(4, 13))), 9))
        assert abs(dip(x) - dip_lp(x)) < 1e-7
    assert abs(dip(np.arange(10.0)) - 1 / 20) < 1e-12  # the minimum, 1/(2n)
    assert abs(dip(np.array([0, 1e-9, 1, 1 + 1e-9])) - 0.25) < 1e-6  # two equal atoms
    x = rng.normal(size=50)
    assert abs(dip(3 * x + 7) - dip(x)) < 1e-12  # affine invariance


def test_mixture_is_deterministic_and_prefers_two_groups_only_when_there_are_two():
    r = np.random.default_rng(2)
    two = np.concatenate([r.normal(0, 1, 500), r.normal(6, 1, 500)])
    a, _ = best(two)
    b, _ = best(two)
    assert a == b and a.k == 2 and abs(a.means[0]) < 0.3 and abs(a.means[1] - 6) < 0.3
    assert best(r.normal(0, 1, 1000))[0].k == 1


def _thr(below, above, events=1000):
    return context(
        spec(),
        [],
        {
            "thresholds": [
                {
                    "key": "approval",
                    "label": "approval limit",
                    "limit": "500000",
                    "band": "50000",
                    "below": below,
                    "above": above,
                    "events": events,
                }
            ]
        },
    )


def test_a_pile_up_under_the_limit_fires_with_no_money():
    (res,), _ = artefact(_thr(80, 20))
    assert float(res.p_value) < 1e-6 and res.money is None and res.strong


def test_a_smooth_density_and_missing_records():
    (res,), _ = artefact(_thr(52, 48))
    assert float(res.p_value) > 0.01
    (res,), _ = artefact(_thr(80, 20, events=150))
    assert not res.eligible and res.reason == "insufficient events"
    for fn, needs in ((artefact, "threshold"), (bimodal, "distribution")):
        out, cov = fn(context(spec(), [], {}))
        assert out == [] and cov.needs == needs
