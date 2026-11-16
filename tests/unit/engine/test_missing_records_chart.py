# SPDX-License-Identifier: Apache-2.0
"""Missing records gets a chart (M11c · A): one account's weekly invoices as bars that stop, then a
dashed "no invoices" box to the run date, built from the run's entity probe only."""

from __future__ import annotations

import datetime as dt

from othor.charts.spec import ChartSpec
from othor.charts.svg import render_svg
from othor.engine.finding_charts import build, load

SPEC = {"metrics": {"net_revenue": {"name": "Net revenue", "unit": "inr"}}}


def _probe(name: str, last: dt.date, weeks: int = 16) -> dict:
    events = []
    for k in range(weeks):
        d = last - dt.timedelta(weeks=k)
        events.append([name, d.isoformat(), 120000.0 + 1000 * k])
        events.append(["Other Mills", d.isoformat(), 999999.0])  # another account: never drawn
    return {"entities": {"customer": {"metric": "net_revenue", "events": events}}}


def _finding(name: str, last: dt.date, end: dt.date, **over) -> dict:
    f = {
        "detector": "missing_records",
        "fingerprint": "fp-ravira",
        "metric": "net_revenue",
        "breakdown": "customer",
        "breakdown_value": name,
        "state": "not_determinable",
        "window": [last.isoformat(), end.isoformat()],
        "slots": {"name": name, "last": last.isoformat(), "gap_days": (end - last).days},
    }
    f.update(over)
    return f


def test_one_quiet_account_gets_bars_that_stop_then_the_gap() -> None:
    last, end = dt.date(2026, 10, 9), dt.date(2026, 10, 31)
    text = build(_finding("Ravira Exports", last, end), None, SPEC, _probe("Ravira Exports", last))  # type: ignore[arg-type]
    assert text is not None
    chart = load(text)
    assert isinstance(chart, ChartSpec) and chart.kind == "gap_block"
    pts = chart.series[0].points
    assert len(pts) == 10
    assert pts[-1][0] == "2026-10-05"  # the Monday of its last invoice
    assert all(v < 999999 for _, v in pts)  # only this account's invoices
    (gap,) = chart.annotations
    assert (gap.from_, gap.to, gap.label) == ("2026-10-12", "2026-10-31", "no invoices")
    assert "none since 9 Oct" in chart.alt_text
    assert "<svg" in render_svg(chart, (520, 160))


def test_the_chart_is_the_same_bytes_every_time() -> None:
    last, end = dt.date(2026, 10, 9), dt.date(2026, 10, 31)
    args = (_finding("Ravira Exports", last, end), None, SPEC, _probe("Ravira Exports", last))
    assert build(*args) == build(*args)  # type: ignore[arg-type]


def test_the_silent_group_and_thin_histories_have_no_chart() -> None:
    last, end = dt.date(2026, 10, 9), dt.date(2026, 10, 31)
    group = _finding("", last, end, breakdown_value=None, state="measured")
    assert build(group, None, SPEC, _probe("Ravira Exports", last)) is None  # type: ignore[arg-type]
    thin = _finding("Ravira Exports", last, end)
    assert build(thin, None, SPEC, _probe("Ravira Exports", last, weeks=2)) is None  # type: ignore[arg-type]
    assert build(thin, None, SPEC, {}) is None  # type: ignore[arg-type]
