# SPDX-License-Identifier: Apache-2.0
"""Summary 1.1 (owner, 21 Nov 2026): the summary card and the lines that explain themselves.
Pure: the card is read from the run's stored results and series, never computed anew; every
figure in its words is inserted from a computed value."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from othor.briefs import values
from othor.charts.svg import render_svg
from othor.engine.store import Point
from othor.web import summary_card as sc
from othor.web import summary_view as sv

D0 = dt.date(2026, 7, 27)  # a Monday
NR = [100, 104, 99, 103, 101, 102, 100, 104, 101, 103, 102, 101, 80]  # ordinary, then a fall


def _spec() -> dict:
    m = {
        "net_revenue": {"name": "Net revenue", "unit": "inr", "area": "Sales", "headline": True},
        "orders": {"name": "Orders", "unit": "count", "area": "Sales"},
        "orders_segment": {"name": "Orders by segment", "unit": "count", "listed": False},
        "freight": {"name": "Freight", "unit": "inr", "listed": False},
        "metres": {"name": "Metres dispatched", "unit": "metres", "area": "Operations"},
        "r60": {"name": "Receivables over 60 days", "unit": "inr", "area": "Collections"},
        "win_rate": {"name": "Win rate", "unit": "percent", "area": "Sales"},
    }
    for v in m.values():
        v.setdefault("status", "active")
    m["win_rate"]["status"] = "needs_source"
    return {"metrics": m, "order": list(m), "ontology_version": 7}


def _pts(metric: str, values: list[float], grain: str = "week") -> list[Point]:
    return [
        Point(metric, grain, "", None, D0 + dt.timedelta(weeks=i), D(str(v)))
        for i, v in enumerate(values)
    ]


def _points() -> dict[str, tuple[Point, ...]]:
    r60 = [61.8, 63.0, 88.4, 90.1, 91.3, 89.7, 93.0, 92.2, 95.2, 90.4, 86.1, 82.5, 79.8]
    orders = [50, 52, 49, 61, 58, 60, 57, 55, 62, 59, 64, 60, 61]
    return {
        "net_revenue": tuple(_pts("net_revenue", NR)),
        "orders": tuple(_pts("orders", orders)),
        "orders_segment": tuple(_pts("orders_segment", orders)),
        "metres": tuple(_pts("metres", [80 + i for i in range(13)])),
        "r60": tuple(_pts("r60", [v * 100000 for v in r60])),
    }


def _result(detector: str, metric: str, **kw) -> dict:
    base = {
        "detector": detector,
        "metric": metric,
        "series": [f"{metric}|week||"],
        "breakdown": "",
        "breakdown_value": None,
        "passed": True,
        "reason": None,
        "strong": False,
        "eligible": True,
        "p_value": "0.00001",
        "q_value": "0.0002",
        "direction": "up",
        "entities": [],
        "slots": {},
    }
    return {**base, **kw}


SHIFT = _result(
    "changepoint",
    "r60",
    slots={
        "change_week": "2026-08-10",
        "before": "6360773.09",
        "after": "9125150.51",
        "unit": "inr",
    },
)
BROKEN = _result(
    "broken_relationship",
    "freight",
    direction="down",
    entities=["freight", "metres"],
    slots={
        "break": "2026-09-14",
        "r_before": "0.780451",
        "r_after": "-0.028571",
        "ratio_change": "0.319423",
        "weeks_before": 20,
        "weeks_after": 6,
    },
)
LEADS = _result(
    "lead_lag",
    "orders",
    direction="none",
    strong=True,
    entities=["orders", "net_revenue"],
    slots={"lag_weeks": 3, "r": "0.72", "leader": "orders", "follower": "net_revenue"},
)


# --- the tiny-base rule ----------------------------------------------------------------------


def test_a_tiny_start_says_from_and_to_not_a_huge_percent():
    assert sv.tiny_base(D(5000), D(9300000))  # under 5% of the later value
    assert sv.tiny_base(D(0), D(10))
    assert sv.tiny_base(D(-200000), D(500000))  # a sign change
    assert not sv.tiny_base(D(90), D(100))
    assert not sv.tiny_base(D(100), D(4))  # a fall to a small value is still a percent
    text, d = sv.change("inr", D(9300000), D(5000), "pct")
    assert text.startswith("from ") and " to " in text and "%" not in text and d == "up"
    text, d = sv.change("inr", D(110), D(100), "pct")
    assert text == "+10%" and d == "up"  # 10% or more: whole
    assert sv.change("inr", D("137.6"), D(100), "pct")[0] == "+38%"
    assert sv.change("inr", D("107.56"), D(100), "pct")[0] == "+7.6%"  # under 10%: one decimal
    # over +100%: from and to, as a tiny start does (the owner's "+1,757%")
    text, d = sv.change("inr", D("437.6"), D(100), "pct")
    assert text == f"from {values.text('inr', D(100))} to {values.text('inr', D('437.6'))}"
    assert d == "up"
    assert sv.change("inr", D(30), D(100), "pct")[0] == sv.fmt.MINUS + "70%"  # a fall stays %
    # the % chart leaves a tiny-base period out instead of a spike
    assert sv.delta("inr", D(9300000), D(5000), "pct") is None
    assert sv.delta("inr", D(110), D(100), "pct") == D("0.1")


# --- what the card reads -----------------------------------------------------------------------


def test_the_week_each_line_shifted_comes_from_sweeps_passed_tests():
    held = {**SHIFT, "metric": "net_revenue", "passed": False, "reason": "snoozed until 2026-12-01"}
    failed = {**SHIFT, "metric": "orders", "passed": False, "reason": "no signal"}
    split = {**SHIFT, "breakdown": "region", "breakdown_value": "South"}
    got = sc.shifts(
        [SHIFT, held, failed, split],
        _spec(),
        start=dt.date(2026, 8, 1),
        end=dt.date(2026, 11, 1),
    )
    assert [s["metric"] for s in got] == ["net_revenue", "r60"] or [s["metric"] for s in got] == [
        "r60",
        "net_revenue",
    ]
    r60 = next(s for s in got if s["metric"] == "r60")
    assert r60["week"] == dt.date(2026, 8, 10) and r60["direction"] == "up"
    assert r60["text"].startswith("Receivables over 60 days stepped up in the week of 10 Aug")
    # inserted from the stored slots, through the one formatter
    assert values.text("inr", D("6360773.09")) in r60["text"]
    assert values.text("inr", D("9125150.51")) in r60["text"]
    # outside the period: not this period's shift
    assert sc.shifts([SHIFT], _spec(), start=dt.date(2026, 9, 1), end=dt.date(2026, 11, 1)) == []
    # with a split, only that value's shifts
    got = sc.shifts(
        [SHIFT, split],
        _spec(),
        start=dt.date(2026, 8, 1),
        end=dt.date(2026, 11, 1),
        pair=("region", "South"),
    )
    assert len(got) == 1 and got[0]["metric"] == "r60"


def test_pairs_are_named_only_when_sweeps_test_passed_and_strong():
    got = sc.pairs([BROKEN, LEADS], _spec())
    assert [p["kind"] for p in got] == ["together", "leads"]
    together, leads = got
    assert together["text"].startswith(
        "Freight and Metres dispatched moved together until the week of 14 Sep"
    )
    assert "32% higher" in together["text"]  # 10% or more: whole
    assert leads["text"] == "Orders has led Net revenue by 3 weeks."
    assert "caused" not in leads["text"]
    # the technical words live behind the (i)
    assert "rank correlation" in together["info"] and "0.78" in together["info"]
    assert "rank correlation" in leads["info"]
    # not passed, weak before the break, or a lead that isn't strong: not named
    assert sc.pairs([{**BROKEN, "passed": False, "reason": "no signal"}], _spec()) == []
    weak = {**BROKEN, "slots": {**BROKEN["slots"], "r_before": "0.41"}}
    assert sc.pairs([weak], _spec()) == []
    assert sc.pairs([{**LEADS, "strong": False}], _spec()) == []


def test_look_alike_numbers_are_flagged_with_their_draft():
    pts = [p for ps in _points().values() for p in ps]
    got = sc.look_alikes(_spec(), pts, {})
    assert len(got) == 1
    a = got[0]
    assert (a["keep"], a["drop"]) == ("orders", "orders_segment")
    assert (
        a["text"]
        == "Orders by segment gives the same numbers as Orders, every week of the last 12."
    )
    assert a["proposal"] is None
    fp = "duplicate:orders:orders_segment"
    got = sc.look_alikes(_spec(), pts, {fp: ("p1", "open")})
    assert got[0]["proposal"] == "p1"
    # the owner kept both (declined): never flagged again
    assert sc.look_alikes(_spec(), pts, {fp: ("p1", "declined")}) == []


def test_the_lead_change_and_the_card():
    spec = _spec()
    pts = _points()
    view = sv.View()
    out = sv.build(spec, pts, view=view)
    lead, more = sc.lead(out["rows"], view)
    # Net revenue fell from 101 to 80 (−20.8%), the biggest move among the lead numbers
    assert lead.startswith("Net revenue fell 21% in the week of 19 Oct")
    assert "biggest move among your lead numbers" in lead
    assert more and more.startswith("Also moving:")
    card = sc.card(
        out["rows"],
        view,
        shifts=sc.shifts([SHIFT], spec, start=dt.date(2026, 7, 1), end=dt.date(2026, 11, 1)),
        pairs=sc.pairs([BROKEN, LEADS], spec),
        alikes=sc.look_alikes(spec, [p for ps in pts.values() for p in ps], {}),
    )
    assert card["kicker"] == "At a glance · 1 Quarter"
    assert [c["label"] for c in card["cols"]] == [
        "Between the numbers",
        "When lines shifted",
        "Same numbers twice",
    ]
    assert card["cols"][2]["alikes"][0]["drop"] == "orders_segment"
    assert card["foot"] == []  # the page title says which run
    # nothing to name: those columns aren't there
    bare = sc.card(out["rows"], view, shifts=[], pairs=[], alikes=[])
    assert bare["cols"] == []


def test_the_card_words_are_plain():
    from tests.guards.test_plain_words import _texts, offenders

    spec = _spec()
    out = sv.build(spec, _points(), view=sv.View())
    card = sc.card(
        out["rows"],
        sv.View(),
        shifts=sc.shifts([SHIFT], spec, start=dt.date(2026, 7, 1), end=dt.date(2026, 11, 1)),
        pairs=sc.pairs([BROKEN, LEADS], spec),
        alikes=[],
    )
    assert offenders(_texts(card)) == []
    for word in ("correlation", "lag", "Spearman", "q "):
        assert all(word not in t for _k, t in _texts(card)), word


# --- the lines -------------------------------------------------------------------------------


def test_a_line_marks_its_turning_point_high_and_low():
    spec = _spec()
    view = sv.View()
    ctx = sv.Ctx(spec, _points(), view, shifts={"r60": (dt.date(2026, 8, 10),)})
    fig = sv.figure(ctx, "r60")
    line, spark = sv.charts(ctx, fig)
    assert line is not None and spark is not None
    kinds = [(a.type, a.label) for a in line.annotations]
    assert ("vline", "10 Aug") in kinds
    labels = [lab for t, lab in kinds if t == "mark"]
    assert len(labels) == 2
    assert labels[0] == "High " + values.text("inr", D("9520000.0"))
    assert labels[1] == "Low " + values.text("inr", D("6180000.0"))
    assert spark.annotations == line.annotations
    svg = render_svg(line, (720, 160))
    assert "10 Aug" in svg and "High " in svg and "<title>" in svg  # hover values stay
    # a flat line has no high or low to mark; a shift outside the window isn't marked
    ctx = sv.Ctx(spec, _points(), view, shifts={"r60": (dt.date(2026, 1, 5),)})
    line, _ = sv.charts(ctx, sv.figure(ctx, "r60"))
    assert line is not None and not any(a.type == "vline" for a in line.annotations)


def test_numbers_waiting_for_data_fold_into_one_line():
    spec = _spec()
    pts = _points()
    del pts["metres"]  # listed, no data yet
    out = sv.build(spec, pts, view=sv.View())
    assert "Operations" not in [r["area"] for r in out["rows"]]
    waiting = {w["metric"]: w["note"] for w in out["waiting"]}
    assert waiting == {"metres": "No data yet", "win_rate": "Needs a source"}
    out = sv.build(spec, pts, view=sv.View(rows="numbers"))
    names = [r["lead"] for g in out["groups"] for r in g["rows"]]
    assert "metres" not in names
    assert {w["metric"] for w in out["waiting"]} == {"metres", "win_rate"}


def test_a_rebound_from_an_unusual_week_is_said_as_one():
    """The 1 Nov night's lesson (owner, 21 Nov 2026): a number back from one unusually low week
    is never "+377.6%"; it is said as a rebound, against the usual level, and a mover whose
    earlier week was ordinary keeps its percent."""
    spec = _spec()
    pts = _points()
    metres = [80, 82, 81, 83, 82, 84, 83, 85, 84, 86, 85, 20, 87]  # one low week, then back
    pts["metres"] = tuple(_pts("metres", metres))
    out = sv.build(spec, pts, view=sv.View())
    row = next(r for r in out["rows"] if r["lead"] == "metres")
    assert (row["unusual"], row["back_near"]) == ("low", True)
    # the column: over +100%, from and to
    assert row["change_text"].startswith("from ")
    lead, more = sc.lead(out["rows"], sv.View())
    assert lead == (
        f"Metres dispatched is back to its usual level ({values.text('metres', D(87))} in the "
        "week of 19 Oct) after an unusually low week."
    )
    assert "%" not in lead and "biggest move" not in lead
    assert more and more.startswith(f"Also moving: Net revenue {sv.fmt.MINUS}21%")
    # a genuine fall below the usual range is not a rebound
    nr = next(r for r in out["rows"] if r["lead"] == "net_revenue")
    assert nr["unusual"] is None
    # a small rebound next to an ordinary mover: the mover leads, the rebound is said after
    small = {**row, "name": "Gross margin", "unusual": "high", "back_near": False}
    small.update(value=D(99), comparison=D(100))
    lead, more = sc.lead([nr, small], sv.View())
    assert lead.startswith("Net revenue fell 21% in the week of 19 Oct")
    assert more == "Gross margin dropped back after an unusually high week."
    # the 1 Nov night's shape: a low week, then a catch-up week well past the usual level
    pts["metres"] = tuple(_pts("metres", [*metres[:-1], 175]))
    out = sv.build(spec, pts, view=sv.View())
    row = next(r for r in out["rows"] if r["lead"] == "metres")
    assert (row["unusual"], row["back_near"]) == ("low", False)
    lead, _ = sc.lead(out["rows"], sv.View())
    assert lead == (
        f"Metres dispatched bounced back to {values.text('metres', D(175))} in the week of 19 Oct"
        f" after an unusually low week ({values.text('metres', D(20))})."
    )
