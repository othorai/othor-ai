# SPDX-License-Identifier: Apache-2.0
"""Weekly forecasts four weeks ahead (v0.12 part 1b, docs/04 §5): one fit per weekly series; its
first week is tonight's row exactly as before (the forecast of record, so Forecast breach doesn't
change), the four weeks go to their own document, the band widens with the horizon, every
period is after the data it was made from, and the same series gives the same bytes."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import numpy as np

from othor.briefs import weather
from othor.engine import orchestrator
from othor.engine.forecast import store
from othor.engine.store import Point
from othor.ontology.compiler.api import canonical

RUN = dt.date(2026, 11, 4)  # a Wednesday: the last complete week started Mon 26 Oct


def _spec() -> dict:
    return {
        "metrics": {
            "rev": {"name": "Revenue", "status": "active", "grain": "week", "unit": "inr"},
            "dso": {"name": "DSO", "status": "active", "grain": "day", "kind": "snapshot"},
        },
        "forecast": {"headline": ["dso", "rev"], "goals": []},
        "suppressions": [],
    }


def _points() -> list[Point]:
    r = np.random.default_rng(3)
    out = []
    for i in range(40):
        p = dt.date(2026, 10, 26) - dt.timedelta(weeks=39 - i)
        v = 1e7 + r.normal(0, 4e5) + i * 1e4
        out.append(Point("rev", "week", "", None, p, Decimal(repr(round(v, 2)))))
        out.append(Point("dso", "week", "", None, p, Decimal(repr(round(40 + r.normal(0, 2), 2)))))
    for i in range(24):
        m = dt.date(2024 + (i + 10) // 12, (i + 10) % 12 + 1, 1)
        out.append(Point("dso", "month", "", None, m, Decimal(repr(round(42 + r.normal(0, 2), 2)))))
    return out


def test_four_weeks_ahead_and_tonight_unchanged() -> None:
    doc, ahead = store.tonight(_spec(), RUN, _points(), kind="nightly", first=False)
    assert doc == store.run(_spec(), RUN, _points(), kind="nightly", first=False)
    assert ahead["weeks"] == store.AHEAD_WEEKS == 4
    rev = [r for r in ahead["rows"] if r["series"] == "rev|week"]
    assert [r["horizon"] for r in rev] == [1, 2, 3, 4]
    assert [r["period"] for r in rev] == ["2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23"]
    # the first week is tonight's row, byte for byte; tonight still stores one week only
    week = [r for r in doc["rows"] if r["series"] == "rev|week"]
    assert len(week) == 1
    assert {k: v for k, v in rev[0].items() if k != "horizon"} == week[0]
    # a day-grain snapshot is forecast at the week: it is in the outlook too
    assert {r["series"] for r in ahead["rows"]} == {"rev|week", "dso|week"}


def test_the_band_widens_with_the_horizon_and_is_stored_before_its_week() -> None:
    _, ahead = store.tonight(_spec(), RUN, _points(), kind="nightly", first=False)
    for series in ("rev|week", "dso|week"):
        rows = [r for r in ahead["rows"] if r["series"] == series]
        widths = [Decimal(r["upper"]) - Decimal(r["lower"]) for r in rows]
        assert widths == sorted(widths) and widths[0] < widths[-1]
        assert all(r["as_of"] < r["period"] for r in rows[1:])


def test_same_series_same_bytes() -> None:
    a = store.tonight(_spec(), RUN, _points(), kind="nightly", first=False)[1]
    b = store.tonight(_spec(), RUN, _points(), kind="nightly", first=False)[1]
    assert canonical(a) == canonical(b)


def test_the_weather_reads_the_file_the_run_writes() -> None:
    assert weather.AHEAD == orchestrator.AHEAD == "forecasts_ahead.json"


def test_a_briefs_headline_number_is_forecast_even_when_the_ontology_marks_none() -> None:
    """v0.19 fix (15 Nov 2026, indigo2): a company whose Ontology marks no headline number and
    has no goal forecast nothing, so What's ahead was empty. The briefs' headline numbers, from
    the run's sealed context, are forecast too, in a fixed order, so replay agrees."""
    spec = {**_spec(), "forecast": {"headline": [], "goals": []}}
    none, _ = store.tonight(spec, RUN, _points(), kind="nightly", first=False)
    assert none["rows"] == []
    also = orchestrator.brief_headlines(
        {"briefs": [{"headline": ["rev"]}, {"headline": ["dso", "rev"]}]}, spec, _points()
    )
    assert also == ("dso", "rev")
    # a brief with no headline numbers chosen shows its first confirmed numbers with data: those
    no_pick = orchestrator.brief_headlines({"briefs": [{"name": "x"}]}, spec, _points())
    assert no_pick == ("dso", "rev")
    doc, _ = store.tonight(spec, RUN, _points(), kind="nightly", first=False, also=also)
    assert {r["series"] for r in doc["rows"] if not r["reconstructed"]} >= {"rev|week", "dso|week"}
    assert set(doc["definitions"]) == {"dso", "rev"}
    # the Ontology's own list is unchanged by `also`; nothing is forecast twice
    both, _ = store.tonight(_spec(), RUN, _points(), kind="nightly", first=False, also=("rev",))
    plain, _ = store.tonight(_spec(), RUN, _points(), kind="nightly", first=False)
    assert canonical(both) == canonical(plain)
