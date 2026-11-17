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


def test_areas_are_kept_only_when_chosen():
    """Advanced Intelligence Phase 0 §8: an older brief's body keeps its bytes (no new version)."""
    old = {"name": "Sales", "scope": {"metrics": None, "exclude": {}}}
    got = edit.clean(old, SPEC, {})
    assert "areas" not in got["scope"]
    assert edit.clean(got, SPEC, {}) == got
    chosen = {
        "name": "Collections",
        "scope": {"metrics": None, "exclude": {}, "areas": ["Collections", " Sales ", "Sales", ""]},
    }
    got = edit.clean(chosen, SPEC, {})
    assert got["scope"]["areas"] == ["Collections", "Sales"]
    assert edit.clean(got, SPEC, {}) == got


AREA_SPEC = {
    "metrics": {
        "net_revenue": {"name": "Net revenue", "status": "active", "area": "Sales"},
        "orders": {"name": "Orders", "status": "active", "area": "Sales"},
        "dso": {"name": "DSO", "status": "active", "area": "Collections"},
        "overdue_amount": {"name": "Overdue amount", "status": "active", "area": "Collections"},
        "headcount": {"name": "Headcount", "status": "active"},
        "helper": {"name": "Helper", "status": "active", "area": "Sales", "listed": False},
        "win_rate": {"name": "Win rate", "status": "needs_source", "area": "Sales"},
    }
}


def test_findings_come_from_lists_the_areas_with_their_counts():
    """Board 4d4: each area of the listed, active numbers (Discover's groups bring theirs),
    "Other numbers" last; all on until the owner turns one off."""
    rows = edit.area_rows(AREA_SPEC, {})
    assert rows == [
        {"area": "Collections", "count": 2, "on": True},
        {"area": "Sales", "count": 2, "on": True},
        {"area": "Other numbers", "count": 1, "on": True},
    ]
    rows = edit.area_rows(AREA_SPEC, {"areas": ["Collections"]})
    assert [r["on"] for r in rows] == [True, False, False]


def test_turning_an_area_off_scopes_the_brief_and_all_on_is_the_whole_company():
    every = ["Collections", "Sales", "Other numbers"]
    form = {"areas_shown": "1", "areas_all": every, "areas": ["Collections"]}
    d = edit.apply_form("numbers", form, {"scope": {"metrics": None, "exclude": {}}})
    assert d["scope"]["areas"] == ["Collections"]
    form = {"areas_shown": "1", "areas_all": every, "areas": every}
    d = edit.apply_form("numbers", form, d)
    assert d["scope"]["areas"] is None
    # a Numbers post without the areas (an older page) leaves them as they were
    d2 = edit.apply_form("numbers", {}, {"scope": {"areas": ["Sales"]}})
    assert d2["scope"]["areas"] == ["Sales"]
