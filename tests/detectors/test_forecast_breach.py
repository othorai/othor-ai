# SPDX-License-Identifier: Apache-2.0
"""Forecast breach (docs/04 §3, M7): a complete period against the forecast stored before it,
money = the distance outside the 80% band; the clean twin and a forecast without a band don't
fire; a goal short of its target by more than the floor fires, within it doesn't."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from othor.engine.detectors.forecast_breach import run
from tests.detectors.synth import context, points, spec

WEEK = dt.date(2026, 10, 19)


def _ctx(actual, record, goals=()):
    doc = spec(rev={"name": "Net revenue", "unit": "inr", "kind": "flow"})
    doc["forecast"] = {"headline": ["rev"]}
    weeks = [WEEK - dt.timedelta(weeks=i) for i in range(5, 0, -1)] + [WEEK]
    pts = points("rev", "week", weeks, [10_000_000] * 5 + [actual])
    c = context(doc, pts)
    c.forecasts = {"of_record": record, "goals": list(goals)}
    return c


REC = [
    {
        "series": "rev|week",
        "metric": "rev",
        "grain": "week",
        "period": WEEK.isoformat(),
        "as_of": (WEEK - dt.timedelta(days=1)).isoformat(),
        "point": "10000000",
        "lower": "9000000",
        "upper": "11000000",
        "method": "ETS(A,N,N)",
        "reconstructed": False,
    }
]


def test_a_week_outside_its_stored_band_fires_with_the_distance_outside():
    (r,), cov = run(_ctx(13_000_000, REC))
    assert float(r.p_value) < 0.01 and r.direction == "up"
    assert Decimal(r.money) == Decimal("2000000.00") and r.window == (
        WEEK,
        WEEK + dt.timedelta(days=6),
    )
    assert cov.status == "ran"
    (clean,), _ = run(_ctx(10_300_000, REC))
    assert clean.money is None and clean.reason == "inside its band"


def test_no_band_or_no_stored_forecast_is_not_tested():
    nob = [{**REC[0], "lower": None, "upper": None}]
    out, cov = run(_ctx(13_000_000, nob))
    assert out == [] and cov.status == "could_not_run"
    out, cov = run(_ctx(13_000_000, []))
    assert (
        out == []
        and cov.needs == "forecast"
        and cov.notes[0].startswith("starts as periods complete")
    )


def test_a_goal_short_by_more_than_its_band_fires():
    g = {
        "key": "fy",
        "name": "₹60 Cr",
        "metric": "rev",
        "aggregate": "fiscal_year_total",
        "direction": "at_least",
        "target": "600000000",
        "by": "2027-03-31",
        "to_date": "329500000",
        "projection": "570000000",
        "lower": "560000000",
        "upper": "580000000",
        "short_by": "30000000",
        "on_track": False,
    }
    out, _ = run(_ctx(10_000_000, [], [g]))
    (goal,) = [r for r in out if r.series == ["goal|fy"]]
    assert float(goal.p_value) < 0.01 and Decimal(goal.money) == Decimal("30000000.00")
    ok = {**g, "projection": "605000000", "short_by": "-5000000", "on_track": True}
    (fine,) = [r for r in run(_ctx(10_000_000, [], [ok]))[0] if r.series == ["goal|fy"]]
    assert fine.money is None and fine.reason == "on track"


def _surat(confirmed: bool, week: dt.date):
    """Weekly net revenue with a catch-up spike in `week`, its stored forecast, and the Surat
    shutdown pattern (10–24 Aug, settles by 7 days)."""
    from othor.engine.detect import run as detect_run

    # broken down by plant: the plant's pattern covers this total (decided 5 Nov 2026)
    doc = spec(rev={"name": "Net revenue", "unit": "inr", "kind": "flow", "breakdowns": ["plant"]})
    doc["forecast"] = {"headline": ["rev"]}
    doc["suppressions"] = [
        {
            "key": "surat_shutdown",
            "name": "Surat plant shutdown, 10–24 August",
            "short": "the Surat shutdown",
            "window": {"kind": "annual", "start": "08-10", "end": "08-24"},
            "settle_days": 7,
            "plant": "Surat",
            "confirmed": confirmed,
            "answered_on": "2026-10-28" if confirmed else None,
        }
    ]
    weeks = [week - dt.timedelta(weeks=i) for i in range(5, 0, -1)] + [week]
    c = context(doc, points("rev", "week", weeks, [10_000_000] * 5 + [16_000_000]))
    c.forecasts = {
        "of_record": [
            {
                **REC[0],
                "period": week.isoformat(),
                "as_of": (week - dt.timedelta(days=1)).isoformat(),
            }
        ]
    }
    (r,) = [x for x in detect_run(c).results if x.detector == "forecast_breach"]
    return r


def test_the_catch_up_after_a_shutdown_is_covered_by_its_settle_by_period():
    catch_up = dt.date(2026, 8, 24)  # the week after the window's last day sits in settle-by
    r = _surat(False, catch_up)
    assert r.passed  # unconfirmed: a finding until the owner answers
    r = _surat(True, catch_up)
    assert not r.passed and r.reason.startswith("suppressed by Surat plant shutdown")
    assert r.slots["covered"]["answered_on"] == "2026-10-28"
    late = _surat(True, dt.date(2026, 8, 31))  # starts after the window: settle-by covers it
    assert not late.passed and late.slots["covered"]["settle"] is True
    # two weeks after the settle-by period (ends 31 Aug): a finding again
    r = _surat(True, dt.date(2026, 9, 14))
    assert r.passed


def _october(month_actual: float):
    """October against its stored forecast, with the Diwali week (19 Oct) 4.0 above its own."""
    from othor.engine.detect import run as detect_run

    doc = spec(rev={"name": "Net revenue", "unit": "inr", "kind": "flow"})
    doc["forecast"] = {"headline": ["rev"]}
    doc["suppressions"] = [
        {
            "key": "diwali_week",
            "name": "Diwali week",
            "window": {"kind": "week_of", "dates": ["2026-10-19"]},
            "confirmed": True,
        }
    ]
    weeks = [dt.date(2026, 10, 19) - dt.timedelta(weeks=i) for i in range(5, -1, -1)]
    pts = points("rev", "week", weeks, [10_000_000] * 5 + [14_000_000])
    months = [dt.date(2026, m, 1) for m in range(5, 11)]
    pts += points("rev", "month", months, [44_000_000] * 5 + [month_actual])
    c = context(doc, pts)
    c.forecasts = {
        "of_record": [
            {**REC[0], "period": "2026-10-19", "as_of": "2026-10-18", "point": "10000000"},
            {
                **REC[0],
                "series": "rev|month",
                "grain": "month",
                "period": "2026-10-01",
                "as_of": "2026-09-30",
                "point": "44000000",
                "lower": "42000000",
                "upper": "46000000",
            },
        ]
    }
    return next(x for x in detect_run(c).results if x.series == ["rev|month||"])


def test_a_month_is_covered_by_a_short_window_only_when_the_window_explains_it():
    # 49.0 − the week's excess 4.0 = 45.0, inside the 42–46 band: covered
    covered = _october(49_000_000)
    assert not covered.passed and covered.reason.startswith("suppressed by Diwali week")
    # 52.0 − 4.0 = 48.0, still above the band: October is news, Diwali only partly explains it
    news = _october(52_000_000)
    assert news.passed and news.slots["partly"] == "Diwali week"
