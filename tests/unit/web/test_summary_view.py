# SPDX-License-Identifier: Apache-2.0
"""Summary's figures (v0.16, M16.2): pure, from stored points. Rows by area in the owner's order
(short list next, own names after, Other numbers last); each lead number's latest complete value
against the period before or the same period a year back, as % or absolute; one breakdown value
at a time; the signed parts that add up to the total; the finding marker; ChartSpecs whose
comparison is re-dated onto this window's dates."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal

import pytest

from othor.charts.svg import render_svg
from othor.core import fmt
from othor.engine.store import Point
from othor.web import summary_view as sv
from othor.web.summary_view import View

MON = dt.date(2026, 10, 26)  # the latest complete week (Monday)


def _weeks(metric, vals, *, end=MON, b="", bv=None):
    n = len(vals)
    return [
        Point(metric, "week", b, bv, end - dt.timedelta(weeks=n - 1 - i), Decimal(str(v)))
        for i, v in enumerate(vals)
    ]


def _months(metric, vals, *, end=dt.date(2026, 10, 1)):
    out, d = [], end
    for _ in range(len(vals) - 1):
        d = (d - dt.timedelta(days=1)).replace(day=1)
    for v in vals:
        out.append(Point(metric, "month", "", None, d, Decimal(str(v))))
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def _by(*lists):
    out = defaultdict(list)
    for pts in lists:
        for p in pts:
            out[p.metric].append(p)
    return {k: tuple(v) for k, v in out.items()}


SPEC = {
    "ontology_version": 7,
    "order": [
        "inv",
        "cn",
        "net_revenue",
        "orders",
        "dso",
        "dso_m",
        "share",
        "cash",
        "loose",
        "helper",
    ],
    "breakdowns": {"region": {"name": "Region"}},
    "forecast": {"headline": ["net_revenue"]},
    "metrics": {
        "inv": {"name": "Invoiced", "unit": "inr", "listed": False, "breakdowns": ["region"]},
        "cn": {"name": "Credit notes", "unit": "inr", "listed": False, "breakdowns": ["region"]},
        "net_revenue": {
            "name": "Net revenue",
            "unit": "inr",
            "area": "Sales",
            "family": "net_revenue",
            "components": ["inv", "cn"],
            "breakdowns": ["region"],
            "terms": [{"metric": "inv", "sign": 1}, {"metric": "cn", "sign": -1}],
        },
        "orders": {"name": "Orders", "unit": "count", "area": "Sales"},
        "dso": {"name": "DSO", "unit": "days", "area": "Collections"},
        "dso_m": {
            "name": "DSO monthly",
            "unit": "days",
            "area": "Collections",
            "grain": "month",
            "per_period": True,
        },
        "share": {"name": "Export share", "unit": "percent", "area": "Export desk"},
        "cash": {
            "name": "Cash",
            "unit": "inr",
            "area": "Finance",
            "grain": "month",
            "kind": "snapshot",
        },
        "loose": {"name": "Loose", "unit": "count"},
        "helper": {"name": "Helper", "unit": "count", "listed": False},
    },
}

INV = [100 + i for i in range(30)]
CN = [10] * 30
POINTS = _by(
    _weeks("inv", INV),
    _weeks("cn", CN),
    _weeks("net_revenue", [a - b for a, b in zip(INV, CN, strict=True)]),
    _weeks("net_revenue", [50] * 29 + [60], b="region", bv="South"),
    _weeks("inv", [55] * 29 + [66], b="region", bv="South"),
    _weeks("cn", [5] * 29 + [6], b="region", bv="South"),
    _weeks("orders", [20] * 60),
    _weeks("dso", [40]),
    _weeks("dso_m", [90] * 5),
    _months("dso_m", [44, 46, 45, 46]),
    _weeks("share", [0.30] * 29 + [0.32]),
    _months("cash", [5_00_00_000] * 14),
    _weeks("loose", [3, 4]),
)


def _row(out, area):
    return next(r for r in out["rows"] if r["area"] == area)


# --- the view --------------------------------------------------------------------------------


def test_view_url_over_saved_over_defaults_and_bad_values_fall_back():
    # owner, 14 Nov 2026: the period alone by default, no comparison line
    # #65 (owner, 14 Nov 2026): Actual by default; a view saved before it keeps its % or ABS
    assert View.parse(None) == View("13w", "none", "actual", "")
    assert View.parse({}, {"show": "pct"}).show == "pct"
    assert View().basis == "before" and View(compare="year").basis == "year"
    saved = {"window": "12m", "compare": "year", "show": "abs"}
    assert View.parse({}, saved) == View("12m", "year", "abs", "")
    assert View.parse({"window": "4w"}, saved).window == "4w"
    assert View.parse({"window": "9y", "show": "x"}, saved) == View("12m", "year", "abs", "")
    splits = {"region": ["South"]}
    assert View.parse({"split": "region=South"}, None, splits).pair == ("region", "South")
    assert View.parse({"split": "region=Mars"}, None, splits).split == ""


# --- rows by area --------------------------------------------------------------------------


def test_areas_in_order_other_numbers_last_and_hidden_counted():
    out = sv.build(SPEC, POINTS)
    assert [r["area"] for r in out["rows"]] == [
        "Sales",
        "Collections",
        "Finance",
        "Export desk",
        "Other numbers",
    ]
    lay = {"order": ["Finance", "Sales"], "hidden": ["Collections"]}
    out = sv.build(SPEC, POINTS, layout=lay)
    assert [r["area"] for r in out["rows"]] == ["Finance", "Sales", "Export desk", "Other numbers"]
    assert out["hidden"] == ["Collections"]


def test_the_lead_number_headline_first_then_the_owners_pick():
    out = sv.build(SPEC, POINTS)
    sales = _row(out, "Sales")
    assert sales["lead"] == "net_revenue"
    assert sales["metrics"] == [("net_revenue", "Net revenue"), ("orders", "Orders")]
    out = sv.build(SPEC, POINTS, layout={"leads": {"Sales": "orders"}})
    assert _row(out, "Sales")["lead"] == "orders"
    out = sv.build(SPEC, POINTS, layout={"leads": {"Sales": "gone"}})  # no longer there
    assert _row(out, "Sales")["lead"] == "net_revenue"


# --- the figures -----------------------------------------------------------------------------


def test_figures_equal_the_stored_points_against_the_period_before():
    r = _row(sv.build(SPEC, POINTS), "Sales")
    assert r["value"] == Decimal(119) and r["comparison"] == Decimal(118)
    assert r["period"] == MON and r["period_label"] == "week of 26 Oct"
    assert r["change_text"] == fmt.pct(Decimal(1) / 118, decimals=1, sign=True)
    assert r["direction"] == "up" and r["vs"] == "vs week of 19 Oct"


def test_absolute_and_points_for_a_share():
    r = _row(sv.build(SPEC, POINTS, view=View(show="abs")), "Sales")
    assert r["change_text"].startswith("+") and "₹" in r["change_text"]
    share = _row(sv.build(SPEC, POINTS, view=View(show="abs")), "Export desk")
    assert share["change_text"] == "+2.0 pts"
    flat = _row(sv.build(SPEC, POINTS, view=View(show="abs")), "Other numbers")
    assert flat["change_text"] == "+1"


def test_same_time_last_year_for_weeks_and_months():
    orders = sv.build(
        SPEC, POINTS, view=View(compare="year"), layout={"leads": {"Sales": "orders"}}
    )
    r = _row(orders, "Sales")
    assert r["comparison"] == Decimal(20) and r["change_text"] == "no change"
    assert r["vs"] == "vs week of 27 Oct last year"
    cash = _row(sv.build(SPEC, POINTS, view=View(window="12m", compare="year")), "Finance")
    assert cash["grain"] == "month" and cash["vs"] == "vs Oct 2025"
    assert sv.year_back("week", MON).weekday() == 0


def test_no_data_for_last_year_and_building_history():
    r = _row(sv.build(SPEC, POINTS, view=View(compare="year")), "Sales")
    assert r["comparison"] is None and r["vs"] == "No data for last year"
    assert r["chart"] is not None and len(r["chart"].series) == 1  # no grey line
    dso = _row(sv.build(SPEC, POINTS), "Collections")
    assert dso["vs"] == "Building history" and dso["change_text"] == ""


def test_a_number_over_the_periods_length_stays_at_its_own_grain():
    """DSO (× days in period) at a week is receivables over one week's revenue: not the DSO the
    owner knows. It stays monthly, and says so."""
    out = sv.build(SPEC, POINTS, layout={"leads": {"Collections": "dso_m"}})
    r = _row(out, "Collections")
    assert r["grain"] == "month" and r["value"] == Decimal(46) and r["note"] == "Monthly only"


def test_a_monthly_only_number_says_so():
    cash = _row(sv.build(SPEC, POINTS), "Finance")
    assert cash["grain"] == "month" and cash["note"] == "Monthly only" and cash["periods"] == 3


def test_usd_text(monkeypatch):
    with fmt.money_style("USD"):
        r = _row(sv.build(SPEC, POINTS, view=View(show="abs")), "Sales")
    assert "$" in r["value_text"] and r["change_text"].startswith("+") and "$" in r["change_text"]


# --- the split -------------------------------------------------------------------------------


def test_one_breakdown_value_and_not_split_by():
    view = View(split="region=South")
    out = sv.build(SPEC, POINTS, view=view)
    sales = _row(out, "Sales")
    assert sales["value"] == Decimal(60) and sales["comparison"] == Decimal(50)
    assert sales["change_text"] == "+20.0%"
    dso = _row(out, "Collections")
    assert dso["note"] == "Not split by Region" and dso["value"] == Decimal(40)


# --- expand: the signed parts ----------------------------------------------------------------


@pytest.mark.parametrize("split", ["", "region=South"])
def test_the_signed_parts_add_up_to_the_total(split):
    ctx = sv.Ctx(SPEC, POINTS, View(split=split))
    ex = sv.expand(ctx, ["net_revenue", "orders"], "net_revenue")
    total = sv.figure(ctx, "net_revenue")["value"]
    assert [(p["name"], p["sign"]) for p in ex["parts"]] == [("Invoiced", 1), ("Credit notes", -1)]
    assert sum(p["sign"] * p["value"] for p in ex["parts"]) == total
    assert [o["metric"] for o in ex["others"]] == ["orders"]


def test_a_number_with_no_terms_expands_to_the_others_only():
    ex = sv.expand(sv.Ctx(SPEC, POINTS, View()), ["net_revenue", "orders"], "orders")
    assert ex["parts"] == [] and [o["metric"] for o in ex["others"]] == ["net_revenue"]


# --- the finding marker ----------------------------------------------------------------------


def test_the_marker_by_metric_family_part_and_split():
    def f(metric, **kw):
        return {"metric": metric, "status": "open", "fingerprint": f"fp-{metric}", **kw}

    for findings in ([f("net_revenue")], [f("cn")], [f("x", family="net_revenue")]):
        r = _row(sv.build(SPEC, POINTS, findings=findings), "Sales")
        assert r["finding"]["count"] == 1
    assert _row(sv.build(SPEC, POINTS, findings=[f("orders")]), "Sales")["finding"] is None
    closed = [{**f("net_revenue"), "status": "resolved"}]
    assert _row(sv.build(SPEC, POINTS, findings=closed), "Sales")["finding"] is None
    north = [f("net_revenue", breakdown="region", breakdown_value="North")]
    view = View(split="region=South")
    assert _row(sv.build(SPEC, POINTS, findings=north, view=view), "Sales")["finding"] is None
    assert (
        _row(sv.build(SPEC, POINTS, findings=north), "Sales")["finding"]["fp"] == "fp-net_revenue"
    )


# --- the charts ------------------------------------------------------------------------------


def test_no_comparison_line_by_default():
    r = _row(sv.build(SPEC, POINTS), "Sales")
    assert len(r["chart"].series) == 1 and len(r["spark"].series) == 1
    assert r["comparison"] == Decimal(118)  # the change is still against the period before


def test_the_comparison_is_re_dated_onto_this_window():
    r = _row(sv.build(SPEC, POINTS, run_id="run-1", view=View(compare="before")), "Sales")
    now, comp = r["chart"].series
    assert len(now.points) == 13 and comp.role == "muted"
    assert [p[0] for p in comp.points] == [p[0] for p in now.points]  # same dates, same axis
    # the grey line is the 13 weeks before
    assert comp.points[-1][1] < now.points[-1][1]
    assert r["chart"].provenance == {
        "metric_id": "net_revenue",
        "run": "run-1",
        "spec_version": "7",
        "window": "13w",
        "compare": "before",
        "split": "",
    }
    spark = r["spark"]
    assert spark.kind == "sparkline" and [x.style for x in spark.series] == ["smooth", "dotted"]
    assert spark.series[1].role == "muted" and spark.series[1].points == comp.points


def test_the_smooth_curve_passes_through_every_point_and_never_overshoots():
    """Smoothing is drawing only (owner, 14 Nov 2026): the monotone curve goes through each
    stored value and stays between two neighbours, so no value appears that isn't there."""
    import re

    from othor.charts.svg import _smooth

    xy = [(0.0, 10.0), (10.0, 30.0), (20.0, 30.0), (30.0, 5.0), (40.0, 20.0)]
    path = _smooth(xy)
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", path)]
    ends = [(nums[0], nums[1])] + [(nums[i + 4], nums[i + 5]) for i in range(2, len(nums) - 2, 6)]
    assert ends == xy
    # every control point stays inside its segment's y range: no overshoot
    for i in range(len(xy) - 1):
        c = nums[2 + 6 * i : 2 + 6 * i + 6]
        lo, hi = sorted((xy[i][1], xy[i + 1][1]))
        assert lo - 1e-6 <= c[1] <= hi + 1e-6 and lo - 1e-6 <= c[3] <= hi + 1e-6


def test_every_chart_renders_the_same_bytes_twice():
    for view in (View(), View("4w", "year", "abs"), View("12m"), View(split="region=South")):
        for r in sv.build(SPEC, POINTS, view=view)["rows"]:
            for c in (r["chart"], r["spark"]):
                if c is not None:
                    assert render_svg(c, (600, 120)) == render_svg(c, (600, 120))


def test_a_day_against_the_day_before_and_the_same_weekday_last_year():
    days = [
        Point(
            "orders",
            "day",
            "",
            None,
            dt.date(2026, 10, 31) - dt.timedelta(days=400 - i),
            Decimal(i),
        )
        for i in range(401)
    ]
    pts = {**POINTS, "orders": tuple(days)}
    out = sv.build(SPEC, pts, view=View(window="7d"), layout={"leads": {"Sales": "orders"}})
    r = _row(out, "Sales")
    assert r["grain"] == "day" and r["periods"] == 7 and r["vs"] == "vs 30 Oct"
    assert len(r["spark"].series[0].points) == 7
    yb = sv.year_back("day", dt.date(2026, 10, 31))
    assert yb.weekday() == dt.date(2026, 10, 31).weekday() and yb == dt.date(2025, 11, 1)
    r = _row(
        sv.build(
            SPEC,
            pts,
            view=View(window="14d", compare="year"),
            layout={"leads": {"Sales": "orders"}},
        ),
        "Sales",
    )
    assert r["vs"] == "vs 1 Nov last year" and r["comparison"] == Decimal(400 - 364)


def test_a_ratio_by_the_day_shows_by_the_week():
    """M16.3: a ratio (a derived number that isn't a sum) over one day's figures misleads
    (gross margin per metre read −₹69.5 for a day); the daily windows show it by the week."""
    spec = {
        **SPEC,
        "metrics": {**SPEC["metrics"], "orders": {**SPEC["metrics"]["orders"], "type": "derived"}},
    }
    days = tuple(
        Point("orders", "day", "", None, dt.date(2026, 10, 1) + dt.timedelta(days=i), Decimal(i))
        for i in range(30)
    )
    pts = {**POINTS, "orders": POINTS["orders"] + days}
    r = _row(
        sv.build(spec, pts, view=View(window="7d"), layout={"leads": {"Sales": "orders"}}), "Sales"
    )
    assert r["grain"] == "week" and r["note"] == "Weekly only"


# --- Every number (owner, 14 Nov 2026): an extra view, a row per number -----------------------


def test_every_number_view_groups_each_listed_number_under_its_area():
    assert View.parse({"rows": "numbers"}).rows == "numbers"
    assert View.parse({"rows": "x"}).rows == "areas"
    assert "groups" not in sv.build(SPEC, POINTS)  # the By area view is unchanged
    out = sv.build(SPEC, POINTS, view=View(rows="numbers"))
    got = [(g["area"], [r["lead"] for r in g["rows"]]) for g in out["groups"]]
    assert got == [
        ("Sales", ["net_revenue", "orders"]),
        ("Collections", ["dso", "dso_m"]),
        ("Finance", ["cash"]),
        ("Export desk", ["share"]),
        ("Other numbers", ["loose"]),
    ]
    sales = out["groups"][0]["rows"]
    assert all(r["spark"] is not None for r in sales) and sales[1]["name"] == "Orders"


def test_hidden_numbers_and_areas_leave_the_every_number_view():
    lay = {"hidden": ["Finance"], "hidden_numbers": ["orders"]}
    out = sv.build(SPEC, POINTS, view=View(rows="numbers"), layout=lay)
    assert [g["area"] for g in out["groups"]][:2] == ["Sales", "Collections"]
    assert [r["lead"] for r in out["groups"][0]["rows"]] == ["net_revenue"]
    assert out["groups"][0]["count"] == 2
    assert out["hidden"] == ["Finance"] and out["hidden_numbers"] == [("orders", "Orders")]


# --- Actual · % · ABS (#65, owner 14 Nov 2026) ----------------------------------------------


def test_actual_plots_values_and_states_the_change_in_percent():
    r = _row(sv.build(SPEC, POINTS, view=View(compare="before")), "Sales")
    assert r["change_text"] == fmt.pct(Decimal(1) / 118, decimals=1, sign=True)
    assert [s.style for s in r["chart"].series] == ["smooth", "dotted"]
    assert not r["chart"].y.zero_line and not r["spark"].y.zero_line
    assert r["chart"].series[0].points[-1][1] == pytest.approx(
        119 / 1e5
    )  # 0.28.4: small money charts in lakh


def test_percent_plots_each_week_against_the_one_before():
    for compare in ("none", "before"):  # Compare off and Period before both mean the week before
        r = _row(sv.build(SPEC, POINTS, view=View(show="pct", compare=compare)), "Sales")
        line, spark = r["chart"], r["spark"]
        assert len(line.series) == 1 and line.series[0].style == "smooth"  # no dotted line
        assert line.y.zero_line and spark.y.zero_line and line.unit.type == "percent"
        got = [v for _d, v in line.series[0].points]
        assert got == pytest.approx([1 / (v - 1) for v in range(107, 120)])
        assert line.series[0].points[-1][0] == MON.isoformat()
        assert spark.series[0].points == line.series[0].points  # the phone shows the same
        # the column and the chart's last point agree
        assert r["change_text"] == fmt.pct(Decimal(str(got[-1])), decimals=1, sign=True)
        assert line.provenance["show"] == "pct"


def test_abs_plots_the_change_in_the_numbers_unit_and_points_for_a_share():
    r = _row(sv.build(SPEC, POINTS, view=View(show="abs")), "Sales")
    assert r["chart"].unit.type == "currency"
    assert [v for _d, v in r["chart"].series[0].points] == pytest.approx(
        [1 / 1e5] * 13
    )  # in lakh (0.28.4)
    share = _row(sv.build(SPEC, POINTS, view=View(show="abs")), "Export desk")
    vals = [v for _d, v in share["chart"].series[0].points]
    assert vals[-1] == pytest.approx(2.0) and set(vals[:-1]) == {0.0}
    assert share["chart"].unit.type == "count"  # points, not a percentage


def test_last_year_compares_each_week_with_the_same_week_a_year_back():
    out = sv.build(
        SPEC, POINTS, view=View(show="pct", compare="year"), layout={"leads": {"Sales": "orders"}}
    )
    pts = _row(out, "Sales")["chart"].series[0].points
    # 60 weeks of orders: only the last 8 of the 13 have a week 52 weeks back
    assert len(pts) == 8 and {v for _d, v in pts} == {0.0}
    # net revenue has 30 weeks: no week a year back, so no change chart
    assert (
        _row(sv.build(SPEC, POINTS, view=View(show="pct", compare="year")), "Sales")["chart"]
        is None
    )


def test_a_change_chart_draws_its_zero_line():
    r = _row(sv.build(SPEC, POINTS, view=View(show="abs")), "Export desk")
    for c, size in ((r["chart"], (720, 160)), (r["spark"], (360, 64))):
        svg = render_svg(c, size)
        assert svg.count("<line") >= 1 and "<circle" in svg
    actual = render_svg(_row(sv.build(SPEC, POINTS), "Export desk")["spark"], (360, 64))
    assert "<line" not in actual
