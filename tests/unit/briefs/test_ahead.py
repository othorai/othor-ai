# SPDX-License-Identifier: Apache-2.0
"""v0.19 (M19.4): What's ahead, one row per headline number, built by code from forecasts.json."""

from __future__ import annotations

import copy
import datetime as dt
import re
from decimal import Decimal

from othor.briefs import ahead
from othor.engine.finding_charts import load
from othor.engine.store import Point

TODAY = dt.date(2026, 11, 1)

SPEC = {
    "metrics": {
        "net_revenue": {"name": "Net revenue", "unit": "inr", "grain": "month"},
        "dso": {"name": "DSO", "unit": "days", "grain": "month"},
        "metres": {"name": "Metres dispatched", "unit": "metres", "grain": "week"},
        "gm": {"name": "Gross margin", "unit": "inr", "grain": "month", "sources": ["MIS"]},
    },
    "suppressions": [
        {"key": "skarfab", "name": "Skarfab paused orders", "metrics": []},
        {"key": "diwali", "name": "Diwali week", "metrics": ["metres"]},
    ],
}


def _track(level: str = "high", source: str = "backtest") -> dict:
    return {"source": source, "n": 12, "inside": 10, "level": level, "typical_error_pct": "8"}


def _doc() -> dict:
    v2 = {"p10": "45000000", "p50": "48000000", "p90": "51000000"}
    return {
        "rows": [
            {"series": "net_revenue|month", "period": "2026-11-01", "v2": v2},
            {"series": "net_revenue|month", "period": "2026-12-01", "v2": v2},
            {
                "series": "dso|month",
                "period": "2026-11-01",
                "v2": {"p10": "40", "p50": "43", "p90": "46"},
            },
            {
                "series": "metres|week",
                "period": "2026-10-26",
                "v2": {"p10": "74778", "p50": "100000", "p90": "125290"},
            },
            {"series": "gm|month", "period": "2026-11-01", "v2": v2},
        ],
        "goals_v2": [
            {
                "metric": "net_revenue",
                "key": "fy_revenue",
                "probability": 5,
                "projection": "567000000",
                "p10": "541000000",
                "p90": "592000000",
                "target": "600000000",
                "by": "2027-03-31",
                "to_date": True,
                "direction": "at_least",
            }
        ],
        "parts": {
            "net_revenue|month": {
                "biggest_mover": {"name": "Invoiced value", "part_goes": "down"},
                "parts": [{"name": "Invoiced value", "p50": "48000000"}],
            }
        },
        "known_ahead": {
            "net_revenue|month": {
                "2026-11-01": [{"key": "skarfab", "name": "Skarfab paused orders", "effect": None}]
            },
            "metres|week": {
                "2026-10-26": [
                    {"key": "diwali", "name": "Diwali week", "effect": "1.4000"},
                    {"key": "skarfab", "name": "Skarfab paused orders", "effect": None},
                ]
            },
        },
        "selection": {
            "net_revenue|month": {
                "method": "ETS(A,N,N)",
                "baseline": "seasonal-naive",
                "mae": "0.73",
                "baseline_mae": "1",
                "periods": 11,
                "calibration": {"k": "1.2", "inside": 10, "of": 12},
            }
        },
        "track": {
            "net_revenue|month": _track("high"),
            "dso|month": _track("medium", "real"),
            "metres|week": _track("low"),
        },
        "abstain": {"gm|month": "source_stale"},
    }


def _points() -> list[Point]:
    out = [
        Point(
            "net_revenue",
            "month",
            "",
            None,
            dt.date(2025, 11, 1) + dt.timedelta(days=31 * i),
            Decimal("46000000"),
        )
        for i in range(12)
    ]
    out += [
        Point(
            "metres",
            "week",
            "",
            None,
            dt.date(2026, 8, 3) + dt.timedelta(weeks=i),
            Decimal("110000"),
        )
        for i in range(12)
    ]
    return out


VERSION = {"headline": ["net_revenue", "dso", "metres", "gm"], "goals": ["fy_revenue"]}


def _rows() -> dict[str, dict]:
    return {r["metric"]: r for r in ahead.rows(SPEC, VERSION, _doc(), _points(), today=TODAY)}


def test_a_goal_number_reads_its_goal():
    r = _rows()["net_revenue"]
    assert r["grain"] == "month"
    assert r["says"].startswith("₹56.7 Cr expected for FY27 (₹54.1–59.2 Cr)")
    assert "95% likely to miss the ₹60 Cr goal" in r["says"]
    assert "mostly lower invoiced value" in r["says"]


def test_a_monthly_number_with_no_goal_reads_next_month():
    r = _rows()["dso"]
    assert r["grain"] == "month" and r["says"] == "40–46 d expected in Nov"


def test_metres_read_whole_as_the_tile_writes_them():
    """0.28.4 (100-night test): "0.75–1.25 L m" beside the tile's "1,75,436 m"."""
    assert _rows()["metres"]["says"].startswith("74,778–1,25,290 m expected this week")
    assert ahead._metres(Decimal(74778)) == "74,778 m"
    assert ahead._metres(Decimal(175436)) == "1,75,436 m"


def test_an_abstain_says_why_with_no_number():
    r = _rows()["gm"]
    assert r["says"] is None and r["why"] == "Waiting for MIS to update"
    assert not re.search(r"\d", r["why"])
    assert "chart" not in r and "confidence" not in r


def test_the_three_words():
    rows = _rows()
    assert rows["net_revenue"]["confidence"]["word"] == "Reliable"
    assert rows["dso"]["confidence"]["word"] == "Fair"
    assert rows["metres"]["confidence"]["word"] == "Rough"
    assert rows["dso"]["confidence"]["why"].startswith("inside its range")
    assert rows["metres"]["confidence"]["why"].startswith("tested on its last")


def test_a_pattern_listing_numbers_is_on_its_row_one_listing_none_once_under():
    rows = _rows()
    assert "includes Diwali week" in rows["metres"]["says"]
    assert all("Skarfab" not in (r.get("says") or "") for r in rows.values())
    note = ahead.note(SPEC, _doc(), list(rows.values()))
    assert note == (
        "Known ahead for the whole business: Skarfab paused orders (its effect not yet measured)."
    )


def test_drivers_and_patterns_only_when_stored():
    d = _doc()
    d.pop("parts")
    d.pop("known_ahead")
    r = {x["metric"]: x for x in ahead.rows(SPEC, VERSION, d, _points(), today=TODAY)}
    assert "mostly" not in r["net_revenue"]["says"] and "includes" not in r["metres"]["says"]
    assert ahead.note(SPEC, d, list(r.values())) is None


def test_the_depth_is_plain_first_and_the_terms_only_in_technical():
    dp = _rows()["net_revenue"]["depth"]
    assert dp["method"] == (
        "It follows the recent level, 27% more accurate on this number's own past than one that "
        "repeats the same period last year."
    )
    assert "widened 1.2×" in dp["range"]
    assert dp["technical"].startswith("ETS(A,N,N) · 80% range (P10–P90)")


JARGON = re.compile(r"\b(P10|P50|P90|ETS|MAE|RMSE|sigma|σ|quantile|calibration|naive)\b", re.I)


def test_no_jargon_on_the_surface():
    rows = _rows()
    for r in rows.values():
        for text in (r.get("says"), r.get("why"), (r.get("confidence") or {}).get("why")):
            assert not JARGON.search(text or ""), text
        for k in ("method", "range", "last", "changed"):
            assert not JARGON.search((r.get("depth") or {}).get(k) or "")
    assert not JARGON.search(ahead.note(SPEC, _doc(), list(rows.values())) or "")


def test_the_chart_runs_actuals_into_the_forecast_with_its_range():
    c = load(_rows()["net_revenue"]["chart"])
    actual, fc = c.series
    assert actual.id == "actual" and len(actual.points) == 12
    assert fc.style == "dotted" and fc.points[0] == actual.points[-1]
    assert c.band is not None


def test_deterministic_and_the_input_is_untouched():
    d = _doc()
    before = copy.deepcopy(d)
    a = ahead.rows(SPEC, VERSION, d, _points(), today=TODAY)
    b = ahead.rows(SPEC, VERSION, d, _points(), today=TODAY)
    assert a == b and d == before


def test_at_most_six_and_only_active_numbers():
    spec = copy.deepcopy(SPEC)
    spec["metrics"]["dso"]["status"] = "retired"
    r = ahead.rows(spec, VERSION, _doc(), _points(), today=TODAY)
    assert [x["metric"] for x in r] == ["net_revenue", "metres", "gm"]


def test_a_brief_with_no_headline_numbers_follows_its_first_confirmed_numbers():
    """v0.19 fix (15 Nov 2026, indigo2): a brief whose owner chose no headline numbers shows its
    first confirmed numbers with data; What's ahead and the forecasts follow those tiles."""
    from othor.briefs.assemble import FIRST_METRICS, tile_metrics

    metrics = {k: {**m, "status": "active"} for k, m in SPEC["metrics"].items()}
    spec = {**SPEC, "metrics": metrics, "order": ["gm", "net_revenue", "dso", "metres"]}
    pts = _points()  # data for net_revenue and metres only
    assert tile_metrics(spec, {"headline": ["dso"]}, pts) == ["dso"]  # chosen: as chosen
    assert tile_metrics(spec, {"headline": []}, pts) == ["net_revenue", "metres"][:FIRST_METRICS]


def test_counts_read_as_whole_numbers():
    """15 Nov 2026 (indigo2): "Active customers: 105.8–122.2 expected" became "106–122"."""
    assert ahead._band("count", "105.8", "122.2") == "106–122"
    assert ahead._one("count", "114.4") == "114"
