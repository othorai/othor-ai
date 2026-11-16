# SPDX-License-Identifier: Apache-2.0
"""v0.19 (M19.2): the chance of hitting a goal, what drives a total, what's known ahead, the days
in the month, what changed since the last brief, and a definition that changed. Pure."""

from __future__ import annotations

import datetime as dt

from othor.engine.forecast import store as fstore
from othor.engine.forecast.ahead import known_ahead
from othor.engine.forecast.changes import changes
from othor.engine.forecast.goals_v2 import goals, probability
from othor.engine.forecast.parts import drivers, reconcile


def _weeks(n: int, first: dt.date = dt.date(2024, 1, 1)) -> list[dt.date]:
    return [first + dt.timedelta(weeks=i) for i in range(n)]


# --- the chance of hitting a goal ---------------------------------------------------------------


def test_probability_is_bounded_and_flips_with_direction():
    assert probability(200, 10, 100, "at_least") == 99
    assert probability(50, 10, 100, "at_least") == 1
    assert probability(100, 10, 100, "at_least") == 50
    assert probability(50, 10, 100, "at_most") == 99
    assert probability(100, 0, 100, "at_least") == 99  # no spread: on target counts as hit


def test_a_fiscal_year_goal_adds_actuals_and_the_remaining_months_sds():
    spec = {
        "forecast": {
            "goals": [
                {
                    "key": "fy",
                    "metric": "rev",
                    "target": "1000",
                    "direction": "at_least",
                    "by": "2027-03-31",
                    "aggregate": "fiscal_year_total",
                }
            ]
        }
    }
    actuals = {"rev": [(dt.date(2026, m, 1), 80.0) for m in range(4, 11)]}  # Apr–Oct: 560
    v2 = {
        "rev": [
            {"period": f"20{y}-{m:02d}-01", "p10": "70", "p50": "80", "p90": "90"}
            for y, m in ((26, 11), (26, 12), (27, 1), (27, 2), (27, 3))
        ]
    }
    (g,) = goals(spec, actuals, v2, {})
    assert g["projection"] == "960.00" and g["to_date"] == "560.00"
    sd = 5 * 20 / 2.563
    assert abs(float(g["p90"]) - (960 + 1.2816 * sd)) < 0.01
    assert g["probability"] == probability(960, sd, 1000, "at_least") < 50
    (g2,) = goals(spec, actuals, v2, {"rev|month": "too_wide"})
    assert g2["probability"] is None and g2["why"] == "too_wide"


# --- the parts ----------------------------------------------------------------------------------


def test_parts_are_reconciled_to_the_total_exactly():
    parts = [("inv", 1, 100.0), ("cn", -1, 10.0), ("reb", -1, 5.0)]
    rec = reconcile(80.0, parts)
    assert abs(sum(s * v for _, s, v in rec) - 80.0) < 1e-9


def test_the_biggest_mover_is_named_with_its_direction():
    d = drivers(
        80.0,
        100.0,
        [("inv", 1, 85.0, 110.0), ("cn", -1, 10.0, 10.0)],
        {"inv": "Invoiced value", "cn": "Credit notes"},
    )
    b = d["biggest_mover"]
    assert b["metric"] == "inv" and b["part_goes"] == "down" and b["total_goes"] == "down"
    assert abs(sum(r["sign"] * float(r["p50"]) for r in d["parts"]) - 80.0) < 1e-5


# --- what's known ahead -------------------------------------------------------------------------


def test_a_pattern_seen_twice_has_its_uplift_measured():
    periods = _weeks(110)
    values = [100.0] * 110
    # Diwali weeks in 2024 and 2025 doubled the number
    for d in (dt.date(2024, 10, 28), dt.date(2025, 10, 20)):
        values[periods.index(d - dt.timedelta(days=d.weekday()))] = 200.0
    spec = {
        "suppressions": [
            {
                "key": "diwali",
                "name": "Diwali week",
                "window": {"kind": "week_of", "dates": ["2024-10-31", "2025-10-21", "2026-11-09"]},
            }
        ]
    }
    future = [dt.date(2026, 11, 9) - dt.timedelta(days=dt.date(2026, 11, 9).weekday())]
    got = known_ahead(spec, "rev", "week", periods, values, future)
    (entry,) = got[future[0].isoformat()]
    assert entry["key"] == "diwali" and entry["occurrences"] == 2 and entry["effect"] == "2.0000"


def test_a_new_pattern_is_named_without_an_effect():
    periods = _weeks(60)
    spec = {
        "suppressions": [
            {
                "key": "x",
                "name": "Shutdown",
                "window": {"kind": "range", "start": "2025-03-03", "end": "2025-03-09"},
            }
        ]
    }
    got = known_ahead(spec, "rev", "week", periods, [100.0] * 60, [dt.date(2025, 3, 3)])
    (entry,) = got["2025-03-03"]
    assert entry["effect"] is None and entry["occurrences"] == 0


# --- days in the month ------------------------------------------------------------------------


def test_a_monthly_flow_can_be_forecast_per_day():
    months, d = [], dt.date(2023, 1, 1)
    for _ in range(36):
        months.append(d)
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    # a steady 10 a day: the monthly total just follows the days in the month
    values = [10.0 * fstore._days(m, "month") for m in months]
    got, _ = fstore.best_series(months, values, "month", 2, mask=[False] * 36, band=True, flow=True)
    assert got is not None and got.params["per_day"] is True
    jan, feb = got.points  # after Dec 2025: Jan and Feb 2026
    assert round(float(jan.point)) == 310 and round(float(feb.point)) == 280


# --- what changed since the last brief ---------------------------------------------------------


def _doc(p50: str, last: str, method: str = "ETS(A,N,N)") -> dict:
    return {
        "rows": [
            {
                "series": "rev|week",
                "metric": "rev",
                "period": "2026-11-09",
                "reconstructed": False,
                "v2": {"p10": "90", "p50": p50, "p90": "110"},
            }
        ],
        "inputs": {"last": {"rev|week": last}, "patterns": "a", "goals": {"rev": "g"}},
        "selection": {"rev|week": {"method": method}},
        "definitions": {"rev": {"hash": "h"}},
    }


def test_a_meaningful_move_says_why_and_a_small_one_isnt_reported():
    got = changes(_doc("108", "2026-11-02", "ETS(A,A,N)"), _doc("100", "2026-10-26"))
    assert got["rev|week"]["why"] == ["new_actuals", "method_changed"]
    assert got["rev|week"]["from"] == "100" and got["rev|week"]["to"] == "108"
    assert changes(_doc("101", "2026-11-02"), _doc("100", "2026-10-26")) == {}
    assert changes(_doc("108", "2026-11-02"), None) == {}


# --- a definition that changed ------------------------------------------------------------------


def test_a_recent_definition_change_says_nothing_yet():
    spec = {"forecast": {"headline": ["rev"]}, "metrics": {"rev": {"definition_hash": "new"}}}
    prev = {"definitions": {"rev": {"hash": "old", "since": None}}}
    d = fstore._definitions(spec, dt.date(2026, 11, 2), prev)
    assert d["rev"] == {"hash": "new", "since": "2026-11-02"}
    weeks = _weeks(20, dt.date(2026, 6, 22))
    assert fstore._definition_abstain(d["rev"], weeks) == "definition_changed"
    old = {"hash": "new", "since": "2026-07-01"}
    assert fstore._definition_abstain(old, weeks) is None
    same = fstore._definitions(spec, dt.date(2026, 11, 3), {"definitions": d})
    assert same["rev"]["since"] == "2026-11-02"  # carried forward
