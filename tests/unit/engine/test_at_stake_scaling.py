# SPDX-License-Identifier: Apache-2.0
"""#138 (owner, 25 Nov 2026): a step in a number with no ₹ of its own puts at risk the share of
its linked ₹ that the size of the move says, capped; never the linked ₹ in full. A share moves in
points of the whole (3.2% → 3.8%: 0.6% of the linked ₹), a count, a duration or a ratio by its
move as a share of where it was (1,000 → 800 orders: 20%); never more than half (`MAX_AT_RISK`).
Runs whose stored context has no money rule replay with the rule of 24 Nov (the linked ₹ in full,
changepoint v2)."""

from __future__ import annotations

import numpy as np
import pytest

from othor.engine.detectors import common
from othor.engine.sweep import current_versions, run_sweep
from tests.detectors.synth import RUN, context, noise, points, spec, weeks

W = weeks(52)


# --- the fraction, per kind -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("unit", "before", "after", "want", "how"),
    [
        ("percent", 0.032, 0.038, 0.006, "share"),  # ABG: scans away 3.2% → 3.8%
        ("percent", 0.82, 0.59, 0.23, "share"),  # truck fill 82% → 59%
        ("ratio", 0.40, 0.30, 0.10, "share"),  # a ratio inside 0–1 is a share of a whole
        ("ratio", 2.0, 2.5, 0.25, "relative"),  # a ratio over 1 moves as a share of its level
        ("count", 1000, 800, 0.20, "relative"),
        ("metres", 50_000, 60_000, 0.20, "relative"),
        ("days", 4.0, 5.0, 0.25, "relative"),  # a duration: 4 → 5 days
        ("percent", 1.20, 1.50, 0.25, "relative"),  # a "percent" over 100%: not a share
    ],
)
def test_the_fraction_per_kind(unit, before, after, want, how):
    got, kind = common.at_risk_fraction(unit, before, after)
    assert kind == how
    assert abs(got - want) < 1e-9


def test_no_move_puts_nothing_at_risk():
    assert common.at_risk_fraction("count", 500, 500) == (0.0, "relative")
    assert common.at_risk_fraction("percent", 0.1, 0.1) == (0.0, "share")


def test_from_zero_the_move_has_no_level_and_cannot_be_sized():
    """Owner, 25 Nov 2026: a step from a level of 0 puts no money at risk; the finding says the
    money can't be sized (not the cap)."""
    got, kind = common.at_risk_fraction("count", 0.0, 12.0)
    assert kind == "relative" and got is None
    assert common.at_risk_fraction("count", 0.0, 0.0) == (0.0, "relative")


def _from_zero_world():
    from tests.detectors.synth import spec as sspec

    complaints = {
        "name": "Complaints",
        "kind": "flow",
        "unit": "count",
        "agg": "sum",
        "at_stake": {"metric": "sales"},
    }
    sp = sspec(complaints=complaints, sales={"name": "Sales", "kind": "flow", "unit": "inr"})
    x = np.zeros(52)
    x[-6:] = 12 + noise(14, 6, 1)  # none for a year, then about 12 a week
    pts = points("complaints", "week", W, x) + points("sales", "week", W, np.full(52, 1e6))
    return sp, pts


def test_a_step_from_zero_is_reported_with_money_that_cannot_be_sized():
    from othor.engine.detectors.changepoint import run

    sp, pts = _from_zero_world()
    (r,) = [y for y in run(context(sp, pts))[0] if y.metric == "complaints"]
    assert r.money is None and r.slots["money_unsized"] is True
    assert r.params["at_risk_scaling"] == "from_zero" and r.params["linked_money"] == "6000000.00"
    out = run_sweep(sp, "h", RUN, [], pts, {}, {})
    (f,) = [f for f in out.findings if f["metric"] == "complaints"]
    assert f["money"] is None and f["status"] == "open"  # not under the floor: unsized
    assert f["text"]["money"] == ""
    assert f["text"]["line"].endswith("The money at risk can't be sized: it started from 0.")
    # a run made with version 2 keeps its rule: the linked ₹ in full
    old = run_sweep(sp, "h", RUN, [], pts, {}, {"detector_versions": {"changepoint": "2"}})
    (o,) = [f for f in old.findings if f["metric"] == "complaints"]
    assert abs(float(o["money"]) - 6_000_000) < 1 and "money_unsized" not in o["slots"]


# --- a step the way a goal wants puts no money at risk (owner, 25 Nov 2026) ----------------


def _goal(direction: str, metric: str = "away") -> dict:
    return {
        "key": f"g_{direction}",
        "name": "A goal",
        "metric": metric,
        "target": "0.03",
        "direction": direction,
        "by": "2027-03-31",
        "aggregate": "value",
    }


def test_a_step_the_way_a_goal_wants_puts_no_money_at_risk():
    ctx, _ = _share_world()  # scans away rises 3.2% → 3.8%
    ctx.spec["goals"] = [_goal("at_least")]  # the owner wants more of it: rising is good
    r = _away(ctx)
    assert r.money is None and r.money_basis == ""
    assert r.params["at_risk_direction"] == "good: up, as the goal g_at_least wants"
    assert "money_unsized" not in r.slots


def test_a_step_against_a_goal_keeps_its_money():
    ctx, _ = _share_world()
    ctx.spec["goals"] = [_goal("at_most")]  # wants less: rising is bad
    r = _away(ctx)
    assert 0.005 * 480_000_000 < float(r.money) < 0.007 * 480_000_000
    assert r.params["at_risk_direction"] == "bad: up, against the goal g_at_most"


def test_goals_that_disagree_or_none_leave_the_direction_unknown():
    ctx, _ = _share_world()
    ctx.spec["goals"] = [_goal("at_least"), _goal("at_most")]
    r = _away(ctx)
    assert r.money is not None and "at_risk_direction" not in r.params
    ctx.spec["goals"] = [_goal("at_least", metric="sales")]  # another number's goal
    assert "at_risk_direction" not in _away(ctx).params


def test_the_test_tab_says_when_the_money_cannot_be_sized_or_went_the_good_way():
    from othor.engine.detectors.changepoint import run
    from othor.web.sweep_view import at_risk_setting

    sp, pts = _from_zero_world()
    (r,) = [y for y in run(context(sp, pts))[0] if y.metric == "complaints"]
    assert at_risk_setting(r.params, sp) == (
        "Money at risk",
        "Can't be sized: the number started from 0 · linked to Sales (₹60 L since the change)",
    )
    ctx, _ = _share_world()
    ctx.spec["goals"] = [_goal("at_least")]
    _, text = at_risk_setting(_away(ctx).params, {"metrics": {}}) or ("", "")
    assert text.startswith("None: it moved the way its goal wants · linked to sales (₹48")


def test_a_run_made_with_version_2_ignores_the_goal():
    ctx, _ = _share_world(version="2")
    ctx.spec["goals"] = [_goal("at_least")]
    assert abs(float(_away(ctx).money) - 480_000_000) < 1


def test_the_cap():
    assert common.MAX_AT_RISK == 0.5
    got, _ = common.at_risk_fraction("count", 1000, 100)  # a 90% fall
    assert abs(got - 0.9) < 1e-9  # the raw fraction is kept for provenance
    assert common.capped(got) == 0.5
    assert common.capped(0.006) == 0.006


# --- the detector: the ABG case --------------------------------------------------------------


def _share_world(version: str | None = None):
    """Scans away from the dealer's district: 3.2% of scans, 3.8% for the last 8 weeks; linked to
    primary sales of about ₹48 Cr over those 8 weeks (₹6 Cr a week)."""
    away = {"kind": "flow", "unit": "percent", "agg": "avg", "at_stake": {"metric": "sales"}}
    sp = spec(away=away, sales={"kind": "flow", "unit": "inr", "agg": "sum"})
    x = 0.032 + noise(11, 52, 0.0004)
    x[-8:] += 0.006
    sales = np.full(52, 60_000_000.0)
    ctx = context(sp, points("away", "week", W, x) + points("sales", "week", W, sales))
    if version is not None:
        ctx.versions = {"changepoint": version}
    return ctx, x


def _away(ctx):
    from othor.engine.detectors.changepoint import run

    (r,) = [x for x in run(ctx)[0] if x.metric == "away"]
    return r


def test_the_abg_case_puts_about_the_move_in_points_at_risk_not_all_sales():
    ctx, x = _share_world()
    assert not ctx.versions  # a new run: the current version
    r = _away(ctx)
    linked = 60_000_000.0 * 8  # ₹48 Cr since the change
    move = float(np.mean(x[-8:]) - np.mean(x[-34:-8]))
    assert r.direction == "up" and r.slots["weeks_held"] == 8
    assert abs(float(r.money) - move * linked) < 0.0001 * move * linked  # ≈ 0.6% of ₹48 Cr ≈ ₹29 L
    assert 0.005 * linked < float(r.money) < 0.007 * linked
    assert r.version == "3"
    # provenance: the linked ₹, the scaling and the cap, as parameters
    p = r.params
    assert p["linked_metric"] == "sales" and p["linked_money"] == "480000000.00"
    assert p["at_risk_scaling"] == "share" and p["at_risk_cap"] == "0.5"
    assert abs(float(p["at_risk_fraction"]) - move) < 1e-6
    assert p["at_risk_fraction_used"] == p["at_risk_fraction"]
    assert r.money_basis == f"0.6% of sales at risk for away since {W[-8].isoformat()}"


def test_the_old_rule_still_prices_the_whole_linked_money():
    """A run made with changepoint 2 (24 Nov to #138) replays with the linked ₹ in full."""
    ctx, _ = _share_world(version="2")
    r = _away(ctx)
    assert abs(float(r.money) - 480_000_000) < 1 and r.version == "2"
    assert r.money_basis == f"sales at risk for away since {W[-8].isoformat()}"
    assert "linked_money" not in r.params


def test_a_count_that_collapses_is_capped_at_half():
    from othor.engine.detectors.changepoint import run

    orders = {"kind": "flow", "unit": "count", "agg": "sum", "at_stake": {"metric": "sales"}}
    sp = spec(orders=orders, sales={"kind": "flow", "unit": "inr", "agg": "sum"})
    x = 1000 + noise(12, 52, 10)
    x[-6:] = 100 + noise(13, 6, 10)  # a 90% fall, six weeks ago
    sales = np.full(52, 1_000_000.0)
    ctx = context(sp, points("orders", "week", W, x) + points("sales", "week", W, sales))
    (r,) = [y for y in run(ctx)[0] if y.metric == "orders"]
    assert abs(float(r.money) - 0.5 * 6_000_000) < 1
    assert (
        float(r.params["at_risk_fraction"]) > 0.85
        and r.params["at_risk_fraction_used"] == "0.500000"
    )
    assert r.money_basis.startswith("50% of sales at risk")


# --- replay: the rule a run uses is the one its stored context names ------------------------


def _sweep_world():
    away = {
        "name": "Scans away",
        "kind": "flow",
        "unit": "percent",
        "agg": "avg",
        "at_stake": {"metric": "sales"},
    }
    sp = spec(away=away, sales={"name": "Sales", "kind": "flow", "unit": "inr", "agg": "sum"})
    x = 0.032 + noise(11, 52, 0.0004)
    x[-8:] += 0.006
    pts = points("away", "week", W, x) + points("sales", "week", W, np.full(52, 60_000_000.0))
    return sp, pts


def _cp(out):
    return next(r for r in out.results if r["detector"] == "changepoint" and r["metric"] == "away")


def test_the_versions_a_context_names_are_the_rules_the_sweep_runs():
    sp, pts = _sweep_world()
    assert current_versions() == {"changepoint": "3", "rising_variance": "2"}
    v1 = run_sweep(sp, "h", RUN, [], pts, {}, {"detector_versions": {"changepoint": "1"}})
    old = run_sweep(sp, "h", RUN, [], pts, {}, {"detector_versions": {"changepoint": "2"}})
    new = run_sweep(sp, "h", RUN, [], pts, {}, {"detector_versions": current_versions()})
    o, n, first = _cp(old), _cp(new), _cp(v1)
    assert first["version"] == "1" and first["money"] is None  # before 24 Nov: no money
    assert o["version"] == "2" and abs(float(o["money"]) - 480_000_000) < 1
    assert n["version"] == "3" and float(n["money"]) < 0.01 * 480_000_000
    assert _cp(run_sweep(sp, "h", RUN, [], pts, {}, {}))["money"] == n["money"]  # none named
    # the same inputs give the same bytes under each version
    again = run_sweep(sp, "h", RUN, [], pts, {}, {"detector_versions": {"changepoint": "2"}})
    assert again.doc["results_hash"] == old.doc["results_hash"]
    assert again.doc["findings_hash"] == old.doc["findings_hash"]


def test_the_test_tab_shows_the_linked_money_the_scaling_and_the_cap():
    from othor.web.sweep_view import at_risk_setting

    r = _away(_share_world()[0])
    sp = {"metrics": {"sales": {"name": "Primary sales"}}}
    name, text = at_risk_setting(r.params, sp) or ("", "")
    assert name == "Money at risk"
    assert text.startswith("0.6% of Primary sales (₹48")
    assert "the move in points of the whole" in text and text.endswith("never more than 50%")
    assert at_risk_setting(_away(_share_world(version="2")[0]).params, sp) is None
