# SPDX-License-Identifier: Apache-2.0
"""The outlook, next four weeks (v0.12 part 1b): each week's scene is the worst among its items
(weekly forecasts against the plan, goal dates, consequence dates, watches, known patterns), a
band too wide to call is Limited view, "likely" only when the whole band misses, "could" when the
point does; the Ahead line is the most important item. Computed values only; same input, same
bytes. Also the leftovers from 1a: a new source → First brief, a goal back on track → Recovering."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any

from othor.briefs import outlook, weather
from othor.engine.store import Point
from othor.ontology.compiler.api import canonical
from tests.unit.briefs.test_weather import RUN, _days, _f, _goal, _near, _outputs, _spec

# RUN is Fri 13 Nov: the next 28 days from the morning after, 14–20 Nov, 21–27 Nov,
# 28 Nov – 4 Dec, 5–11 Dec (owner, 12 Nov 2026); a weekly forecast sits where its Sunday is
WEEK1 = dt.date(2026, 11, 9)
PLAN = Decimal("70000000")  # (₹240 Cr − ₹89 Cr to date) over 151 days, × 7 = ₹7 Cr a week


def _fy_goal(**kw: Any) -> dict[str, Any]:
    return {
        "key": "fy",
        "name": "Net revenue ₹240 Cr this year",
        "metric": "net_revenue",
        "aggregate": "fiscal_year_total",
        "direction": "at_least",
        "target": "2400000000",
        "by": "2027-03-31",
        "to_date": "890000000",
        "projection": "2300000000",
        "lower": "2200000000",
        "upper": "2500000000",
        "on_track": False,
        **kw,
    }


def _months() -> list[Point]:
    return [
        Point("net_revenue", "month", "", None, dt.date(2026, m, 1), Decimal("200000000"))
        for m in range(4, 11)
    ]


def _weekly(metric: str = "net_revenue", n: int = 12) -> list[Point]:
    return [
        Point(metric, "week", "", None, WEEK1 - dt.timedelta(weeks=n - i), Decimal("68000000"))
        for i in range(n)
    ]


def _rows(*weeks: tuple[str, str | None, str | None], metric: str = "net_revenue") -> dict:
    return {
        "format": 1,
        "weeks": 4,
        "rows": [
            {
                "series": f"{metric}|week",
                "metric": metric,
                "grain": "week",
                "period": (WEEK1 + dt.timedelta(weeks=h)).isoformat(),
                "horizon": h + 1,
                "point": p,
                "lower": lo,
                "upper": hi,
                "method": "ETS(A,N,N)",
                "aicc": "1.0",
                "params": {"sigma": "1", "n": 40, "seasonal": False},
                "as_of": (RUN - dt.timedelta(days=1)).isoformat(),
                "reconstructed": False,
            }
            for h, (p, lo, hi) in enumerate(weeks)
        ],
    }


STEADY = _rows(
    ("60000000", "55000000", "65000000"),  # likely ₹1 Cr short
    ("66000000", "58000000", "74000000"),  # could be ₹40 L short
    ("75000000", "69000000", "81000000"),  # on plan
    ("70000000", "40000000", "100000000"),  # ±43%: too wide to call
)


def make(
    ahead: dict | None = STEADY,
    goals: list[dict] | None = None,
    findings: list[dict] | None = None,
    spec: dict | None = None,
    watches: list[dict] | None = None,
    status: list[dict] | None = None,
) -> dict[str, Any]:
    return weather.make(
        spec or _spec(),
        RUN,
        kind="nightly",
        first_brief=False,
        findings=findings or [],
        goals=[_fy_goal()] if goals is None else goals,
        outputs_doc=_outputs(),
        points=[*_days(), *_months(), *_weekly()],
        ahead=ahead,
        watches=watches or [],
        watch_status=status or [],
    )


def _scenes(w: dict) -> list[str]:
    return [x["scene"] for x in w["outlook"]["weeks"]]


def test_four_weeks_with_names_dates_and_the_plan() -> None:
    o = make()["outlook"]
    assert [w["name"] for w in o["weeks"]] == ["This week", "Next week", "Week 3", "Week 4"]
    assert [w["dates"] for w in o["weeks"]] == [
        "14–20 Nov",
        "21–27 Nov",
        "28 Nov – 4 Dec",
        "5–11 Dec",
    ]
    assert Decimal(o["plans"]["net_revenue"]["value"]) == PLAN


def test_likely_could_on_plan_and_limited_view() -> None:
    w = make()
    assert _scenes(w) == ["light_rain", "overcast", "clear", "fog"]
    lines = [x["line"] for x in w["outlook"]["weeks"]]
    assert lines[0] == "Net revenue likely ₹1 Cr short of plan"
    assert lines[1] == "Net revenue could be ₹40 L short of plan"
    assert lines[2] == "Net revenue on plan: ₹7.5 Cr against ₹7 Cr"
    assert lines[3] == "Too uncertain to call · bands too wide this far out"
    assert [x["label"] for x in w["outlook"]["weeks"]][3] == "Limited view"
    fog = w["outlook"]["weeks"][3]["items"][0]
    assert fog["verdict"] == "wide" and fog["why"].startswith("The band is ₹4.0–10.0 Cr wide")
    # the worst week, and the Ahead line with its band and the days to it
    assert w["outlook"]["worst"] == 1
    assert w["outlook"]["ahead_line"] == (
        "Net revenue this week likely ₹1 Cr short of plan (80% band ₹0.5–1.5 Cr) · in 2 days."
    )


def test_a_large_shortfall_is_money_at_stake() -> None:
    w = make(ahead=_rows(("40000000", "35000000", "45000000")))
    assert _scenes(w)[0] == "heavy_rain"  # ₹3 Cr short ≥ 10% of a ₹20 Cr month


def test_could_reads_the_band_against_the_plan() -> None:
    w = make(ahead=_rows(("66000000", "58000000", "74000000")))
    assert w["outlook"]["ahead_line"] == (
        "Net revenue this week could be ₹40 L short of plan "
        "(80% band ₹5.8–7.4 Cr against ₹7 Cr) · in 2 days."
    )


def test_no_band_yet_is_limited_view() -> None:
    w = make(ahead=_rows(("60000000", None, None)))
    assert _scenes(w)[0] == "fog"
    assert "starts after 8 weeks of history" in w["outlook"]["weeks"][0]["items"][0]["why"]


def test_a_number_without_a_goal_adds_nothing() -> None:
    w = make(goals=[])
    assert _scenes(w) == ["clear"] * 4 and w["outlook"]["worst"] is None
    assert w["outlook"]["ahead_line"] == "Nothing stands out in the next four weeks."


def test_the_band_setting_comes_from_the_spec() -> None:
    w = make(spec=_spec(weather={"fog_band_share": "0.5"}))
    assert _scenes(w)[3] == "clear"  # ±43% is callable under ±50%: on plan


def test_a_consequence_date_lands_in_its_week() -> None:
    # net hiding gross: the November close, 30 Nov, in Week 3 (28 Nov – 4 Dec); material
    w = make(findings=[_near("a", "14000000")])
    assert _scenes(w)[2] == "heavy_rain"
    assert w["outlook"]["worst"] == 3  # worse than This week's light rain
    assert w["outlook"]["ahead_line"] == (
        "Item a moved 14000000 · November close in 17 days (30 Nov)."
    )
    item = w["outlook"]["weeks"][2]["items"][0]
    assert item["kind"] == "finding" and item["fingerprint"] == "a"


def test_a_goal_date_in_the_window() -> None:
    g = _goal("2000000000", "2200000000", "2100000000", by="2026-11-25")
    w = make(goals=[g])
    assert _scenes(w)[1] == "heavy_rain"  # whole band misses, due 25 Nov (21–27 Nov)
    near = _goal("2000000000", "2200000000", "2100000000", by="2026-11-15")
    assert _scenes(make(goals=[near]))[0] == "thunderstorm"  # due within 7 days


def test_a_watch_projected_to_cross() -> None:
    spec = _spec()
    spec["metrics"]["dso"] = {"name": "DSO", "unit": "days", "type": "derived"}
    ahead = _rows(("44", "43", "45"), ("47", "46", "48"), metric="dso")
    watch = {
        "id": "w1",
        "label": "DSO",
        "threshold": "45",
        "direction": "above",
        "indicator": {"kind": "value", "metric": "dso", "grain": "day"},
    }
    w = make(ahead=ahead, goals=[], spec=spec, watches=[watch])
    assert _scenes(w)[:2] == ["clear", "light_rain"]
    assert w["outlook"]["weeks"][1]["line"] == "DSO likely to cross 45.0"
    # one already breached is today's matter
    w = make(
        ahead=ahead,
        goals=[],
        spec=spec,
        watches=[watch],
        status=[{"id": "w1", "status": "breached"}],
    )
    assert _scenes(w)[:2] == ["clear", "clear"]


def test_a_known_pattern_ahead_is_a_quiet_week() -> None:
    p = {
        "key": "diwali_week",
        "short": "Diwali week",
        "window": {"kind": "week_of", "dates": ["2026-11-25"]},
    }
    w = make(goals=[], spec=_spec(suppressions=[p]))
    assert _scenes(w)[1] == "clear_night"  # the week of 25 Nov overlaps 21–27 Nov
    assert (
        w["outlook"]["weeks"][1]["line"]
        == "Diwali week: a quiet week, as your Business Model expects"
    )


def test_same_input_same_bytes_and_order_free() -> None:
    fs = [_near("b", "14000000"), _near("a", "14000000")]
    a = canonical(make(findings=fs)["outlook"])
    assert a == canonical(make(findings=fs)["outlook"])
    assert a == canonical(make(findings=list(reversed(fs)))["outlook"])


def test_no_number_is_typed_in_a_template() -> None:
    for key, text in outlook.LINES.items():
        assert not re.search(r"\d", text), key
    for key, text in weather.RULES.items():
        assert not re.search(r"\d", text), key


def test_every_number_on_a_card_or_the_ahead_line_is_computed() -> None:
    w = make(findings=[_near("a", "14000000")])
    o = w["outlook"]
    shown = [x["line"] for x in o["weeks"]] + [o["ahead_line"]]
    # the numbers' sources: the forecast rows, the plan, the finding, the run date
    src = canonical(STEADY) + canonical(o["plans"]) + "14000000 17 30 80 2 1 0.5 1.5 0.4 7 7.5 4 10"
    for text in shown:
        for n in re.findall(r"\d+(?:\.\d+)?", text):
            assert n in src, (n, text)


def test_the_forecast_panel_chart_is_a_spec() -> None:
    from othor.charts.svg import render_svg

    w = make()
    item = w["outlook"]["weeks"][1]["items"][0]
    spec = outlook.chart(_spec(), item, STEADY["rows"], [*_weekly()])
    assert spec is not None and spec.kind == "line"
    assert [s.id for s in spec.series] == ["actual", "forecast"]
    assert len(spec.series[1].points) == 3  # the last actual, then weeks 1 and 2
    assert spec.band is not None and spec.annotations[0].label == "plan ₹7 Cr"
    svg = render_svg(spec, (620, 220))
    assert svg.startswith("<svg") and svg == render_svg(spec, (620, 220))


# --- leftovers from part 1a ----------------------------------------------------------------------


def test_a_new_source_is_a_first_brief() -> None:
    def w(prev: list[str]) -> dict:
        return weather.make(
            _spec(),
            RUN,
            kind="nightly",
            first_brief=False,
            findings=[_f("a", "600000")],
            goals=[],
            outputs_doc=_outputs(),
            points=_days(),
            previous={"sources": prev, "goals": {}},
        )

    got = w(["erp_db"])
    assert got["scene"] == "sunrise"
    assert got["line"] == (
        "sheets connected since the last brief: here’s how the business looks with it."
    )
    assert w(["erp_db", "sheets"])["scene"] == "partly_cloudy"


def test_a_goal_back_on_track_is_recovering() -> None:
    def w(was: bool) -> dict:
        return weather.make(
            _spec(),
            RUN,
            kind="nightly",
            first_brief=False,
            findings=[],
            goals=[_goal("2300000000", "2600000000", "2450000000", on_track=True)],
            outputs_doc=_outputs(),
            points=_days(),
            previous={"sources": ["erp_db", "sheets"], "goals": {"fy": was}},
        )

    got = w(False)
    assert got["scene"] == "rainbow" and got["facts"]["recovered"] == ["fy"]
    assert got["line"] == (
        "Net revenue ₹240 Cr this year is back on track since the last brief; nothing new needs "
        "you."
    )
    assert w(True)["scene"] == "clear"


def test_how_this_was_picked_has_the_rule_and_the_nearest_item() -> None:
    w = make(findings=[_near("a", "14000000")])
    assert w["scene"] == "heavy_rain"
    assert w["rule"].startswith("Money at stake: items of at least 1% of a month’s revenue")
    assert w["facts"]["nearest"] == {"date": "2026-11-30", "fingerprint": "a"}


def test_the_spec_overrides_a_threshold() -> None:
    f = [_f("a", "3000000")]  # ₹0.3 Cr: material at 1% of ₹20 Cr
    assert make(findings=f, goals=[], ahead=None)["scene"] == "light_rain"
    w = make(findings=f, goals=[], ahead=None, spec=_spec(weather={"material_share": "0.02"}))
    assert w["scene"] == "partly_cloudy" and w["settings"]["material_share"] == "0.02"
    assert "2% of a month’s revenue" in w["rule"]
