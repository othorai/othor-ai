# SPDX-License-Identifier: Apache-2.0
"""Business weather, today (v0.12 part 1a): the scene is picked by code from the run's outputs,
in the plan's precedence; each line is a template filled with computed values only; the same
inputs give the same bytes."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from othor.briefs import weather
from othor.engine.store import Point
from othor.ontology.compiler.api import canonical

RUN = dt.date(2026, 11, 13)  # a Friday: yesterday, Thu 12 Nov, is normally active
REV = Decimal("200000000")  # ₹20 Cr a month


def _spec(**extra: Any) -> dict[str, Any]:
    return {
        "metrics": {
            "net_revenue": {"name": "Net revenue", "unit": "inr", "type": "derived"},
            "orders": {"name": "Orders", "type": "base", "status": "active", "grain": "day"},
            "receivables": {
                "name": "Receivables",
                "type": "base",
                "kind": "snapshot",
                "status": "active",
            },
        },
        "order": ["net_revenue", "orders", "receivables"],
        "forecast": {"headline": ["net_revenue"], "goals": []},
        "suppressions": [],
        **extra,
    }


def _days(
    quiet_weekdays: tuple[int, ...] = (6,), skip: tuple[dt.date, ...] = (), weeks: int = 14
) -> list[Point]:
    """Daily orders from `weeks` weeks back to yesterday; none on the quiet weekdays (Sunday)."""
    out = []
    d = RUN - dt.timedelta(weeks=weeks)
    while d < RUN:
        v = Decimal(0) if d.weekday() in quiet_weekdays or d in skip else Decimal(10)
        out.append(Point("orders", "day", "", None, d, v))
        out.append(Point("receivables", "day", "", None, d, Decimal(5)))  # never counts
        d += dt.timedelta(days=1)
    return out


def _outputs(
    unread: tuple[str, ...] = (), ran: int = 11, total: int = 11, history: int = 900
) -> dict[str, Any]:
    checks = [{"detector": f"d{i:02d}", "status": "ran"} for i in range(ran)]
    checks += [{"detector": f"x{i:02d}", "status": "could_not_run"} for i in range(total - ran)]
    return {
        "coverage": {
            "sources": {
                "erp_db": {"status": "stale" if "erp_db" in unread else "read"},
                "sheets": {"status": "read"},
            },
            "stale_metrics": ["orders", "receivables"] if unread else [],
        },
        "baselines": {
            "net_revenue|month": {"mean": str(REV), "history_days": history},
        },
        "sweep": {"coverage": checks},
    }


def _f(
    fp: str,
    money: str,
    detector: str = "dip",
    *,
    direction: str = "down",
    window: tuple[dt.date, dt.date] = (RUN - dt.timedelta(days=60), RUN - dt.timedelta(days=1)),
    status: str = "open",
    state: str = "measured",
    title: str = "",
    **more: Any,
) -> dict[str, Any]:
    return {
        "fingerprint": fp,
        "status": status,
        "detector": detector,
        "metric": "net_revenue",
        "direction": direction,
        "state": state,
        "money": money,
        "window": [window[0].isoformat(), window[1].isoformat()],
        "first_seen": {"date": RUN.isoformat()},
        "text": {"title": title or f"Item {fp} moved {money}"},
        **more,
    }


def make(
    findings: list[dict[str, Any]] | None = None,
    goals: list[dict[str, Any]] | None = None,
    outputs: dict[str, Any] | None = None,
    points: list[Point] | None = None,
    spec: dict[str, Any] | None = None,
    first: bool = False,
) -> dict[str, Any]:
    return weather.make(
        spec or _spec(),
        RUN,
        kind="nightly",
        first_brief=first,
        findings=findings or [],
        goals=goals or [],
        outputs_doc=outputs or _outputs(),
        points=_days() if points is None else points,
    )


# a finding with a month-close consequence (net hiding gross: 30 Nov, 17 days away)
def _near(fp: str, money: str) -> dict[str, Any]:
    return _f(fp, money, "net_hiding_gross")


# a finding with no consequence date (beyond 90 days)
def _far(fp: str, money: str, detector: str = "dip", **kw: Any) -> dict[str, Any]:
    return _f(fp, money, detector, **kw)


def _goal(
    lower: str, upper: str, projection: str, on_track: bool = False, by: str = "2027-03-31"
) -> dict[str, Any]:
    return {
        "key": "fy",
        "name": "Net revenue ₹240 Cr this year",
        "metric": "net_revenue",
        "aggregate": "fiscal_year_total",
        "direction": "at_least",
        "target": "2400000000",
        "by": by,
        "projection": projection,
        "lower": lower,
        "upper": upper,
        "on_track": on_track,
    }


# --- each scene is reachable -------------------------------------------------------------------

CASES: dict[str, dict[str, Any]] = {
    "clear": {},
    "partly_cloudy": {"findings": [_far("a", "600000")]},
    "overcast": {"findings": [_far(c, "600000") for c in "abc"]},
    "fog": {"outputs": _outputs(ran=2, total=11)},
    "light_rain": {"findings": [_far("a", "5000000", title="South fell ₹0.5 Cr")]},
    "heavy_rain": {"findings": [_near("a", "14000000"), _far("b", "9000000")]},
    "thunderstorm": {"goals": [_goal("2000000000", "2200000000", "2100000000", by="2026-11-30")]},
    "hail": {
        "findings": [
            _far(
                "a",
                "5000000",
                "changepoint",
                window=(RUN - dt.timedelta(days=3), RUN - dt.timedelta(days=1)),
            )
        ]
    },
    "windy": {"findings": [_far("a", "5000000", "rising_variance", direction="up")]},
    "heat_wave": {"findings": [_far("a", "5000000", "forecast_breach", direction="up")]},
    "rainbow": {
        "findings": [
            _far("a", "5000000", status="resolved", resolved={"date": RUN.isoformat()}),
        ]
    },
    "clear_night": {"points": _days(quiet_weekdays=(3, 6))},  # Thursdays are quiet
    "sunrise": {"first": True},
    "no_signal": {"outputs": _outputs(unread=("erp_db",))},
    "frozen": {"points": _days(skip=(RUN - dt.timedelta(days=1),))},
}


@pytest.mark.parametrize("scene", sorted(CASES))
def test_every_scene_is_reachable(scene: str) -> None:
    w = make(**CASES[scene])
    assert w["scene"] == scene, (w["scene"], w["line"])
    s = weather.SCENES[scene]
    assert (w["file"], w["name"], w["panel"]) == (s.file, s.name, s.panel)
    assert w["line"] and w["why"]


def test_fifteen_scenes_in_one_precedence() -> None:
    assert len(weather.SCENES) == 15 == len(weather.PRECEDENCE)
    assert set(weather.PRECEDENCE) == set(weather.SCENES) == set(CASES)
    assert [s.file[:2] for s in weather.SCENES.values()] == [f"{i:02d}" for i in range(1, 16)]


# --- precedence --------------------------------------------------------------------------------


def test_no_signal_beats_a_thunderstorm() -> None:
    storm = CASES["thunderstorm"]
    assert make(**storm)["scene"] == "thunderstorm"
    w = make(**storm, outputs=_outputs(unread=("erp_db",)))
    assert w["scene"] == "no_signal"
    assert w["line"] == (
        "erp_db couldn’t be read last night; today’s view is missing orders and receivables."
    )


def test_frozen_beats_a_thunderstorm_but_not_no_signal() -> None:
    stop = _days(skip=(RUN - dt.timedelta(days=1),))
    assert make(**CASES["thunderstorm"], points=stop)["scene"] == "frozen"
    w = make(points=stop, outputs=_outputs(unread=("erp_db",)))
    assert w["scene"] == "no_signal"  # guard 3: only when every source was read


def test_frozen_needs_about_ninety_percent_of_the_weekdays() -> None:
    yday = RUN - dt.timedelta(days=1)
    # Thursday active in 11 of the last 12 weeks: 92%, Frozen
    one = (yday - dt.timedelta(weeks=3),)
    assert make(points=_days(skip=(yday, *one)))["scene"] == "frozen"
    # 10 of 12: 83%, a normally quiet-ish day, never Frozen (Clear night)
    two = (yday - dt.timedelta(weeks=3), yday - dt.timedelta(weeks=5))
    assert make(points=_days(skip=(yday, *two)))["scene"] == "clear_night"


def test_frozen_needs_twelve_weeks_of_history() -> None:
    w = make(points=_days(skip=(RUN - dt.timedelta(days=1),), weeks=6))
    assert w["scene"] == "clear"


def test_frozen_never_under_a_known_pattern() -> None:
    yday = RUN - dt.timedelta(days=1)
    pattern = {
        "key": "diwali_week",
        "short": "Diwali week",
        "window": {"kind": "week_of", "dates": [yday.isoformat()]},
    }
    w = make(points=_days(skip=(yday,)), spec=_spec(suppressions=[pattern]))
    assert w["scene"] == "clear_night"
    assert (
        w["line"]
        == "A quiet day: no activity on Thu 12 Nov, as your Ontology expects (Diwali week)."
    )
    # an unconfirmed pattern, or one for one plant, doesn't explain a company-wide stop
    for p in ({**pattern, "confirmed": False}, {**pattern, "plant": "Surat"}):
        assert make(points=_days(skip=(yday,)), spec=_spec(suppressions=[p]))["scene"] == "frozen"


def test_sunrise_on_the_first_brief_under_no_signal() -> None:
    assert make(**CASES["heavy_rain"], first=True)["scene"] == "sunrise"
    assert make(first=True, outputs=_outputs(unread=("erp_db",)))["scene"] == "no_signal"


def test_a_big_item_due_within_days_is_a_thunderstorm() -> None:
    # concentration's consequence is 60 days out; a goal-linked step uses the goal date
    spec = _spec()
    spec["forecast"]["goals"] = [
        {"key": "q", "name": "Net revenue by 18 Nov", "metric": "net_revenue", "by": "2026-11-18"}
    ]
    f = _far("a", "20000000", "changepoint", direction="up", title="Net revenue stepped down")
    w = make(findings=[f], spec=spec)
    assert (
        w["scene"] == "thunderstorm"
        and w["line"] == "Net revenue stepped down. This needs you today."
    )
    assert w["why"] == ["one item", "₹2 Cr", "nearest 18 Nov"]
    # under the big share (5% of ₹20 Cr = ₹1 Cr): material, due within 30 days → Heavy rain
    f2 = {**f, "money": "5000000"}
    assert make(findings=[f2], spec=spec)["scene"] == "heavy_rain"


def test_a_goal_whose_whole_band_misses() -> None:
    w = make(**CASES["thunderstorm"])
    assert w["line"] == (
        "Even at the top of its band, net revenue reaches ₹220 Cr against the ₹240 Cr goal "
        "for 30 Nov."
    )
    assert w["why"] == ["Net revenue: band ₹200 Cr–₹220 Cr", "goal ₹240 Cr by 30 Nov"]
    # due further out than 30 days: Heavy rain, not a storm ("only when the data says today")
    far = make(goals=[_goal("2000000000", "2200000000", "2100000000")])
    assert far["scene"] == "heavy_rain" and far["line"].endswith("goal for 31 Mar.")


def test_heavy_rain_line_and_why() -> None:
    w = make(**CASES["heavy_rain"])
    assert w["line"] == "₹2.3 Cr at stake across two items; the nearest date is 30 Nov."
    assert w["why"] == ["two items", "₹2.3 Cr", "nearest 30 Nov"]


def test_a_large_share_without_dates_is_heavy_rain() -> None:
    w = make(findings=[_far("a", "15000000"), _far("b", "9000000")])
    assert w["scene"] == "heavy_rain"
    assert w["line"] == "₹2.4 Cr at stake across two items, a large share of a month’s revenue."


def test_an_amber_goal_is_overcast() -> None:
    w = make(goals=[_goal("2300000000", "2500000000", "2380000000")])
    assert w["scene"] == "overcast"
    assert w["line"] == "Grey but steady: net revenue is tracking ₹238 Cr against ₹240 Cr."


def test_too_little_history_is_fog() -> None:
    w = make(outputs=_outputs(history=30))
    assert w["scene"] == "fog"
    assert w["line"] == "30 days of history so far; the view clears as more data comes in."


def test_needs_confirmation_never_counts() -> None:
    f = _near("a", "90000000") | {"state": "needs_confirmation"}
    assert make(findings=[f])["scene"] == "clear"


def test_clear_line() -> None:
    assert make()["line"] == "Nothing needs you today. All 11 checks ran clean."


# --- the same input, the same output -----------------------------------------------------------


@pytest.mark.parametrize("scene", sorted(CASES))
def test_same_input_same_bytes(scene: str) -> None:
    a = canonical(make(**CASES[scene]))
    b = canonical(make(**CASES[scene]))
    assert a == b


def test_order_of_findings_does_not_change_the_weather() -> None:
    fs = [_far("b", "5000000", title="B fell"), _far("a", "5000000", title="A fell")]
    assert canonical(make(findings=fs)) == canonical(make(findings=list(reversed(fs))))
    assert make(findings=fs)["line"].startswith("A fell;")


# --- the lines: templates with no typed number -------------------------------------------------


def test_no_number_is_typed_in_a_template() -> None:
    for key, text in weather.LINES.items():
        assert not re.search(r"\d", text), key


@pytest.mark.parametrize("scene", sorted(CASES))
def test_every_number_in_a_line_is_a_computed_one(scene: str) -> None:
    """Each number in the line is in the facts the scene used, or in a finding's own title."""
    case = CASES[scene]
    w = make(**case)
    words = canonical(w["why"]) + canonical(case.get("findings") or [])
    for n in re.findall(r"\d+(?:\.\d+)?", w["line"]):
        assert n in words or n in canonical(w["facts"]), (n, w["line"])


def test_no_shade_over_the_scenes() -> None:
    """Owner, 13 Nov 2026: the text sits straight on the scene, no shade; since 14 Nov 2026 it is
    ink on the light panels (the contrast test below)."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[3] / "othor/web/styles/components.css").read_text()
    assert "--wx-shade" not in css and ".weather::after" not in css


# --- #57 (owner, 14 Nov 2026): a source read fine but with no data for yesterday --------------


def _two_sources(**extra: Any) -> dict[str, Any]:
    """Orders from the Sales sheet, dispatches from erp_db: each base metric names its source."""
    spec = _spec(**extra)
    spec["metrics"]["orders"]["source"] = "Sales sheet"
    spec["metrics"]["dispatches"] = {
        "name": "Dispatches",
        "type": "base",
        "status": "active",
        "source": "erp_db",
    }
    spec["metrics"]["receivables"]["source"] = "erp_db"
    return spec


def _rows(
    metric: str, drop: tuple[dt.date, ...] = (), only: tuple[int, ...] | None = None
) -> list[Point]:
    """A point for each day with rows; a day with no rows has no point at all (never a zero)."""
    out = []
    d = RUN - dt.timedelta(weeks=14)
    while d < RUN:
        if d not in drop and d.weekday() != 6 and (only is None or d.weekday() in only):
            out.append(Point(metric, "day", "", None, d, Decimal(10)))
        d += dt.timedelta(days=1)
    return out


YDAY = RUN - dt.timedelta(days=1)


def test_a_source_with_no_rows_for_yesterday_is_no_signal_not_frozen() -> None:
    w = make(spec=_two_sources(), points=_rows("orders", drop=(YDAY,)) + _rows("dispatches"))
    assert w["scene"] == "no_signal"
    assert w["line"] == (
        "Sales sheet wasn’t updated yesterday; its newest data is from Wed 11 Nov."
    )
    assert w["why"] == ["one source not updated: Sales sheet"]
    fresh = w["facts"]["sources_fresh"]
    assert fresh["Sales sheet"] == {"newest": "2026-11-11", "updated": False, "late": True}
    assert fresh["erp_db"] == {"newest": "2026-11-12", "updated": True, "late": False}


def test_a_few_sources_not_updated_are_named_together() -> None:
    both = _rows("orders", drop=(YDAY,)) + _rows("dispatches", drop=(YDAY,))
    w = make(spec=_two_sources(), points=both)
    assert w["scene"] == "no_signal"
    assert w["line"] == ("A few of your sources weren’t updated yesterday: erp_db and Sales sheet.")


def test_a_source_updated_only_on_its_own_days_is_not_late() -> None:
    # a weekly sheet: rows on Mondays only; Thursday with no rows is its usual quiet
    weekly = _rows("orders", only=(0,))
    w = make(spec=_two_sources(), points=weekly + _rows("dispatches"))
    assert w["scene"] == "clear"
    fresh = w["facts"]["sources_fresh"]["Sales sheet"]
    assert fresh == {"newest": "2026-11-09", "updated": False, "late": False}


def test_no_rows_under_a_known_pattern_is_a_quiet_day() -> None:
    pattern = {
        "key": "diwali_week",
        "short": "Diwali week",
        "window": {"kind": "week_of", "dates": [YDAY.isoformat()]},
    }
    both = _rows("orders", drop=(YDAY,)) + _rows("dispatches", drop=(YDAY,))
    w = make(spec=_two_sources(suppressions=[pattern]), points=both)
    assert w["scene"] == "clear_night"


def test_a_source_that_couldnt_be_read_is_said_first() -> None:
    w = make(
        spec=_two_sources(),
        points=_rows("orders", drop=(YDAY,)) + _rows("dispatches"),
        outputs=_outputs(unread=("erp_db",)),
    )
    assert w["scene"] == "no_signal" and "couldn’t be read" in w["line"]


def test_the_not_updated_rule_reads_the_settings() -> None:
    w = make(spec=_two_sources(), points=_rows("orders", drop=(YDAY,)) + _rows("dispatches"))
    assert w["rule"].startswith("Data missing: a source couldn’t be read last night, or had no")


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[3] / "design").exists(),
    reason="the weather colour sheet lives with the design files; not in the public repository",
)
def test_every_scene_s_text_reads_on_its_panel() -> None:
    """The blueprint pastel set (owner, 14 Nov 2026): ink #1F2430 on every scene's light panel,
    each at least 4.5:1 (WCAG AA); the panels are colours.csv's."""
    import csv
    from pathlib import Path

    from tests.guards.test_a11y import contrast

    rows = {
        r["file"]: r
        for r in csv.DictReader(
            (Path(__file__).resolve().parents[3] / "design/weather-forecast/colours.csv").open()
        )
    }
    for s in weather.SCENES.values():
        assert s.text == "#1F2430", s.key
        assert s.panel == rows[s.file]["panel_hex"].upper(), s.key
        assert contrast(s.text, s.panel) >= 4.5, (s.key, contrast(s.text, s.panel))
