# SPDX-License-Identifier: Apache-2.0
"""A widening spread keeps the start it was first reported with (24 Nov 2026, the 100-night
test): the check looks for a start in its last 8 weeks only, so once the widening is older its
start slid a week every week ("doubled from 17 Aug", then 24 Aug, 31 Aug, "tripled from 7 Sep").
Tonight's statistics and window stay as measured; the words say since when."""

from __future__ import annotations

import datetime as dt

from othor.engine import findings
from othor.engine.sweep_text import words
from tests.unit.engine.test_leaving_findings import _r

D = dt.date
SLOTS = {
    "sd_prior": "0.5",
    "sd_recent": "1.6",
    "mean_recent": "4.3",
    "p90_prior": "4.9",
    "p90_recent": "6.8",
}


def _spread(started: str):
    return _r(
        detector="rising_variance",
        metric="dispatch_time",
        series="dispatch_time|day|plant|Coimbatore",
        slots={**SLOTS, "started": started},
    )


def test_an_open_spread_keeps_its_first_start():
    first = findings.build([_spread("2026-08-17")], [], D(2026, 10, 8))
    assert first[0]["slots"]["started"] == "2026-08-17" and "since" not in first[0]["slots"]
    later = findings.build([_spread("2026-08-24")], first, D(2026, 10, 15))
    assert later[0]["slots"]["started"] == "2026-08-24"  # tonight's own, as measured
    assert later[0]["slots"]["since"] == "2026-08-17"
    again = findings.build([_spread("2026-09-07")], later, D(2026, 10, 31))
    assert again[0]["slots"]["since"] == "2026-08-17"
    spec = {"metrics": {"dispatch_time": {"name": "Dispatch time", "unit": "days"}}}
    assert "from 17 Aug" in words(again[0], spec)["title"]


def test_a_new_spread_has_no_carried_start():
    got = findings.build([_spread("2026-09-07")], [], D(2026, 10, 31))
    assert "since" not in got[0]["slots"]
