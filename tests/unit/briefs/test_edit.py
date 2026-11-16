# SPDX-License-Identifier: Apache-2.0
"""Edit brief's draft (4d2–4d6): every field re-checked against the spec, a tab's fields merged
in, the scope's computed effect, and the "today with this change" line."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from othor.briefs import edit
from othor.engine.store import Point

SPEC = {
    "metrics": {
        "net_revenue": {
            "name": "Net revenue",
            "unit": "inr",
            "grain": "month",
            "status": "active",
            "kind": "flow",
        },
        "dso": {"name": "DSO", "unit": "days", "grain": "month", "status": "active"},
        "win_rate": {"name": "Win rate", "status": "needs_source"},
    },
    "breakdowns": {"region": {"name": "Region"}, "customer": {"name": "Customer", "entity": True}},
    "owners": [{"name": "Priya Menon", "areas": []}],
    "forecast": {
        "goals": [{"key": "fy27_net_revenue", "metric": "net_revenue", "by": "2027-03-31"}]
    },
}
OCT = dt.date(2026, 10, 1)
POINTS = [
    Point("net_revenue", "month", "", None, OCT, Decimal("46200000")),
    Point("net_revenue", "month", "region", "South", OCT, Decimal("10000000")),
    Point("net_revenue", "month", "region", "Export", OCT, Decimal("17600000")),
    Point("net_revenue", "month", "region", "West", OCT, Decimal("18600000")),
    Point("net_revenue", "month", "customer", "Skarfab", OCT, Decimal("1")),
]


def test_scope_options_are_the_low_cardinality_breakdowns():
    assert edit.scope_options(SPEC, POINTS) == {"region": ["Export", "South", "West"]}


def test_clean_drops_what_the_spec_does_not_have():
    got = edit.clean(
        {
            "name": " Sales ",
            "question": "Where is South going?",
            "headline": ["net_revenue", "win_rate", "made_up", "net_revenue"],
            "scope": {"metrics": None, "exclude": {"region": ["Export", "Mars"], "planet": ["x"]}},
            "ask_owner": "Somebody Else",
            "goals": ["fy27_net_revenue", "nope"],
            "schedule": "hourly",
            "time": "25:00",
            "email_format": "full",
        },
        SPEC,
        edit.scope_options(SPEC, POINTS),
    )
    assert got["name"] == "Sales" and got["headline"] == ["net_revenue"]
    assert got["scope"] == {"metrics": None, "exclude": {"region": ["Export"]}}
    assert got["ask_owner"] == "top_item" and got["goals"] == ["fy27_net_revenue"]
    assert (got["schedule"], got["time"], got["email_format"]) == ("daily", "06:00", "full")


def test_a_tab_merges_its_fields_and_the_scope_shows_its_effect():
    draft = {"headline": ["net_revenue", "dso"], "scope": {"metrics": None, "exclude": {}}}
    options = edit.scope_options(SPEC, POINTS)
    d = edit.apply_form("scope", {"kept": {"region": ["South", "West"]}, "options": options}, draft)
    assert d["scope"]["exclude"] == {"region": ["Export"]}
    (rev,) = edit.in_scope_totals(SPEC, POINTS, d)
    assert rev["kept"] == Decimal("28600000") and rev["total"] == Decimal("46200000")
    d = edit.apply_form("numbers", {"remove": "dso"}, d)
    assert d["headline"] == ["net_revenue"]
    d = edit.apply_form("numbers", {"add": "dso"}, d)
    d = edit.apply_form("numbers", {"up": "dso"}, d)
    assert d["headline"] == ["dso", "net_revenue"]
    before = {"title": "A", "items": [1, 2, 3, 4]}
    assert (
        edit.effect(before, {"title": "A", "items": [1, 2, 3]})
        == "Headline unchanged · 4 items → 3"
    )
    assert (
        edit.effect(before, {"title": "B", "items": [1, 2, 3, 4]}) == "Headline changes · 4 items"
    )


def test_weekly_and_monthly_keep_their_day_and_daily_has_none():
    opts = edit.scope_options(SPEC, POINTS)
    w = edit.clean({"schedule": "weekly", "on": "fri"}, SPEC, opts)
    assert (w["schedule"], w["on"]) == ("weekly", "fri")
    m = edit.clean({"schedule": "monthly", "on": "31"}, SPEC, opts)
    assert (m["schedule"], m["on"]) == ("monthly", "1")
    # a daily brief's body is as before: no `on` at all
    assert "on" not in edit.clean({"schedule": "daily", "on": "fri"}, SPEC, opts)
    d = edit.apply_form("delivery", {"schedule": "weekly", "on": "wed"}, {"schedule": "daily"})
    assert (d["schedule"], d["on"]) == ("weekly", "wed")
