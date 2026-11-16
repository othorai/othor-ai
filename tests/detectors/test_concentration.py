# SPDX-License-Identifier: Apache-2.0
"""Concentration: the top 4 above 50% and rising fires, with the value above 50% as money; a
steady spread of customers doesn't."""

import numpy as np

from othor.engine.detectors.concentration import run
from tests.detectors.synth import context, noise, points, spec, weeks

W = weeks(26)


def _ctx(top_trend, seed):
    sp = spec(book={"kind": "snapshot", "unit": "inr", "agg": "sum", "breakdowns": ["customer"]})
    pts = []
    total = np.zeros(26)
    for i in range(12):  # top 4 start at 48% of the book, like P3 in June
        if i < 4:
            v = 7e6 * np.linspace(1, top_trend, 26) + noise(seed + i, 26, 50_000)
        else:
            v = 3.8e6 * np.ones(26) + noise(seed + i, 26, 50_000)
        total += v
        pts += points("book", "week", W, v, "customer", f"C{i:02d}")
    pts += points("book", "week", W, total)
    return context(sp, pts), total


def test_top_four_above_half_and_rising_fires():
    ctx, total = _ctx(1.6, 1)
    (r,), _ = run(ctx)
    assert (
        float(r.p_value) < 1e-6 and r.strong and sorted(r.entities) == ["C00", "C01", "C02", "C03"]
    )
    share = float(r.slots["share_now"])
    assert share > 0.5
    assert abs(float(r.money) - (share - 0.5) * total[-1]) / ((share - 0.5) * total[-1]) < 0.01


def test_a_steady_spread_does_not_fire():
    for seed in range(5):
        ctx, _ = _ctx(1.0, 100 + seed * 20)
        (r,), _ = run(ctx)
        assert float(r.p_value) > 0.001, seed
