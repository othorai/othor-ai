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


def test_the_start_is_when_the_spread_widened_not_where_the_search_begins():
    """25 Nov 2026 (the Coimbatore date that moved): the start was always the first of the 8
    candidate weeks (run date − 8 weeks), because a quiet week passed once the wide weeks after it
    were pooled with it; so "from" slid a week every night, and a widening 30 days old read as 8
    weeks old. The start is the week that best splits narrow from wide."""
    for ago in (30, 40, 50):  # under 4 weeks old it is measured over the last 4 weeks
        for seed in (1, 5, 9):
            x = 4.1 + noise(seed, 140, 0.4)
            x[-ago:] = 4.1 + noise(seed + 1, ago, 1.2)
            (r,), _ = run(_ctx(x))
            began = RUN - dt.timedelta(days=ago)
            start = dt.date.fromisoformat(r.slots["started"])
            assert float(r.p_value) < 1e-4, (ago, seed)
            assert abs((start - began).days) <= 7, (ago, seed, start, began)


def test_a_young_widening_reports_its_own_start_not_the_test_window():
    """A widening under 4 weeks old is tested over the last 4 weeks, but its start (and the money
    since it) is the week it widened, not the window's first day."""
    x = 4.1 + noise(21, 140, 0.4)
    x[-16:] = 4.1 + noise(22, 16, 1.6)
    rev = np.full(140, 200_000.0)
    (r,), _ = run(_ctx(x, rev))
    assert float(r.p_value) < 1e-3
    start = dt.date.fromisoformat(r.slots["started"])
    assert abs((start - (RUN - dt.timedelta(days=16))).days) <= 7, start
    assert abs(float(r.money) - 200_000.0 * (RUN - start).days) < 1


def test_a_run_made_with_version_1_replays_its_start():
    """#138: version 1 stays in the code for runs made with it (before 25 Nov): its start is the
    first candidate week whose spread, pooled with the weeks after it, is wide (here the search's
    first week), and its results say version 1."""
    x = 4.1 + noise(1, 140, 0.4)
    x[-30:] = 4.1 + noise(2, 30, 1.2)
    ctx = _ctx(x)
    (new,), _ = run(ctx)
    ctx.versions = {"rising_variance": "1"}
    (old,), _ = run(ctx)
    assert new.version == "2" and old.version == "1"
    first = RUN - dt.timedelta(weeks=8)
    assert old.slots["started"] == (first - dt.timedelta(days=first.weekday())).isoformat()
    assert new.slots["started"] != old.slots["started"]
    assert old.window[0].isoformat() == old.slots["started"]
