# SPDX-License-Identifier: Apache-2.0
"""One forecast for every goal (21 Nov 2026, indigodemo: the tile said "on track for 45", the goal
row "tracking 39 d · At risk", What's ahead "43 d expected · 61% likely"): the run's goal entries
take the v2 forecast's projection, range and likelihood, so every reader agrees."""

from __future__ import annotations

from othor.engine.forecast.goals_v2 import one_source

V1 = {
    "key": "dso_under_45",
    "name": "DSO under 45",
    "metric": "dso",
    "aggregate": "value",
    "direction": "at_most",
    "target": "45",
    "by": "2027-03-31",
    "to_date": None,
    "projection": "39.00",
    "lower": "32.00",
    "upper": "47.00",
    "short_by": "-6.00",
    "on_track": True,
}
V2 = {
    "key": "dso_under_45",
    "metric": "dso",
    "target": "45",
    "by": "2027-03-31",
    "direction": "at_most",
    "probability": 61,
    "projection": "43.00",
    "p10": "34.00",
    "p90": "52.00",
    "to_date": None,
    "short_by": "-2.00",
}


def test_v2_numbers_and_the_likelihood_win():
    (g,) = one_source([V1], [V2])
    assert (g["projection"], g["lower"], g["upper"]) == ("43.00", "34.00", "52.00")
    assert g["probability"] == 61 and g["short_by"] == "-2.00"
    assert g["on_track"] is False  # 61% is not on track (80% or more is)
    assert g["name"] == "DSO under 45" and g["aggregate"] == "value"  # v1's own fields stay
    (sure,) = one_source([V1], [{**V2, "probability": 85}])
    assert sure["on_track"] is True


def test_without_a_v2_likelihood_the_entry_is_unchanged():
    (g,) = one_source([V1], [{**V2, "probability": None, "why": "too_wide"}])
    assert g == V1
    assert one_source([V1], []) == [V1]
