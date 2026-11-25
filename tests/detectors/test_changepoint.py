# SPDX-License-Identifier: Apache-2.0
"""Changepoint golden tests: a planted step fires (Measured; Assessed when only reconstructed
weeks carry it), with the step as money for a stock; its clean twin doesn't."""

from decimal import Decimal

import numpy as np

from othor.engine.detectors import common
from othor.engine.detectors.changepoint import run
from tests.detectors.synth import context, noise, points, spec, weeks

W = weeks(52)


def _stock(values, reconstructed=False):
    sp = spec(rec={"kind": "snapshot", "unit": "inr", "agg": "sum"})
    return context(sp, points("rec", "week", W, values, reconstructed=reconstructed))


def test_planted_step_fires_measured_with_the_step_as_money():
    x = 5_800_000 + noise(1, 52, 300_000)
    x[-11:] += 4_000_000  # steps up 11 weeks ago, held
    (r,), cov = run(_stock(x))
    assert r.eligible and float(r.p_value) < 1e-6 and r.strong and r.direction == "up"
    assert r.state_hint == "measured" and r.slots["weeks_held"] == 11
    assert r.window[0] == W[-11]
    step = float(np.mean(x[-11:]) - np.mean(x[-37:-11]))
    assert abs(float(r.money) - step) / step < 0.01
    assert cov.status == "ran" and cov.checked == 1


def test_reconstructed_weeks_only_give_assessed():
    x = 5_800_000 + noise(2, 52, 300_000)
    x[-11:] += 4_000_000
    (r,), _ = run(_stock(x, reconstructed=True))
    assert r.reconstructed_only and r.state_hint == "assessed"


def test_clean_twin_does_not_fire():
    for seed in range(10):
        (r,), _ = run(_stock(5_800_000 + noise(100 + seed, 52, 300_000)))
        assert float(r.p_value) > 0.001, seed


def test_a_flow_step_is_money_per_week_times_weeks_held():
    sp = spec(rev={"kind": "flow", "unit": "inr", "agg": "sum"})
    x = 1_000_000 + noise(3, 52, 50_000)
    x[-8:] -= 400_000
    (r,), _ = run(context(sp, points("rev", "week", W, x)))
    assert r.direction == "down" and r.slots["weeks_held"] == 8
    assert abs(float(r.money) - 400_000 * 8) / (400_000 * 8) < 0.05


def test_old_changes_and_short_history():
    sp = spec(rec={"kind": "snapshot", "unit": "inr", "agg": "sum"})
    x = 5_000_000 + noise(4, 52, 100_000)
    x[-40:] += 2_000_000  # 40 weeks ago: not open
    (r,), _ = run(context(sp, points("rec", "week", W, x)))
    assert float(r.p_value) == 1 and r.reason == "the change is older than 26 weeks"
    (r,), cov = run(context(sp, points("rec", "week", weeks(3), [1, 2, 3])))
    assert not r.eligible and r.reason == "insufficient history" and cov.status == "could_not_run"
    assert Decimal(r.p_value or "1") == 1 or r.p_value is None


# A step in a number with no ₹ of its own (truck fill, stock in kg, a count) puts at risk the ₹ of
# the metric it names in `at_stake`, since the change (owner, 24 Nov 2026; as rising variance).
def _linked(link: bool):
    fill = {"kind": "flow", "unit": "percent", "agg": "avg"}
    if link:
        fill["at_stake"] = {"metric": "freight"}
    sp = spec(fill=fill, freight={"kind": "flow", "unit": "inr", "agg": "sum"})
    x = 0.82 + noise(5, 52, 0.01)
    x[-6:] -= 0.23  # truck fill drops from 82% to 59%, six weeks ago
    rupees = np.full(52, 300_000.0)
    return context(sp, points("fill", "week", W, x) + points("freight", "week", W, rupees))


def test_a_non_rupee_step_with_a_link_puts_its_linked_money_at_risk():
    """Since #138 (v3) the share the move says: 82% → 59% fill is 23 points of the whole."""
    (r,) = [x for x in run(_linked(True))[0] if x.metric == "fill"]
    assert r.direction == "down" and r.slots["weeks_held"] == 6
    move = abs(float(r.slots["after"]) - float(r.slots["before"]))
    assert abs(move - 0.23) < 0.01
    assert abs(float(r.money) - move * 300_000 * 6) < 2  # that share of freight since the change
    assert r.money_basis == (
        f"{common.share_text(move)} of freight at risk for fill since {W[-6].isoformat()}"
    )


def test_the_old_rule_put_the_whole_linked_money_at_risk():
    ctx = _linked(True)
    ctx.versions = {"changepoint": "2"}  # a run made with v2 (24 Nov to #138) replays with it
    (r,) = [x for x in run(ctx)[0] if x.metric == "fill"]
    assert abs(float(r.money) - 300_000 * 6) < 1  # the ₹ through freight since the change
    assert r.money_basis == f"freight at risk for fill since {W[-6].isoformat()}"


def test_a_non_rupee_step_without_a_link_has_no_money():
    (r,) = [x for x in run(_linked(False))[0] if x.metric == "fill"]
    assert r.direction == "down" and r.money is None and r.money_basis == ""


def test_the_at_risk_money_reads_as_at_risk():
    from othor.engine.sweep_text import money_tag

    f = {"detector": "changepoint", "money": "1800000", "money_basis": "freight at risk for x"}
    assert money_tag(f).endswith(" at risk")
    assert not money_tag({**f, "money_basis": "weekly step in rev × 8 weeks"}).endswith("risk")
