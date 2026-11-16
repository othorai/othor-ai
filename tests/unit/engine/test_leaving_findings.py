# SPDX-License-Identifier: Apache-2.0
"""How an item leaves Findings (decided 5 Nov 2026):

- Resolved: cleared by data, or explained by an answer that stops testing it (an entity pause);
- Suppressed: covered by a confirmed known pattern (listed once, under the pattern, with the
  nights it was open), or snoozed or muted by the owner;
- Merged: absorbed into another finding; it shows under that finding's "Also seen by".

Covered and merged findings are never Resolved and never counted. A goal's shortfall is tested
and recorded but is never a Sweep finding.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

from othor.engine import detect, findings
from othor.engine.detectors.base import Result

D = dt.date


def _r(reason: str | None = None, *, passed: bool | None = None, **kw) -> Result:
    r = Result(
        detector=kw.get("detector", "forecast_breach"),
        version="1",
        series=[kw.get("series", "metres_dispatched|week||")],
        metric=kw.get("metric", "metres_dispatched"),
        breakdown="",
        breakdown_value=None,
        window=(D(2026, 8, 24), D(2026, 8, 30)),
        statistic="5",
        p_value="0.000001",
        effect="0.4",
        strong=True,
        direction="up",
        money="5300000.00",
        money_basis="",
        family="ops",
    )
    r.passed = reason is None if passed is None else passed
    r.reason = reason
    r.slots = kw.get("slots", {})
    return r


def _night(results, previous, day):
    return {f["fingerprint"]: f for f in findings.build(results, previous, day)}


def test_a_finding_covered_by_a_newly_confirmed_pattern_is_suppressed_not_resolved():
    nights = [D(2026, 10, 23) + dt.timedelta(days=i) for i in range(5)]  # 23–27 Oct
    prev: list = []
    for day in nights:
        prev = list(_night([_r()], prev, day).values())
    (open_,) = prev
    assert open_["status"] == "open" and open_["days_open"] == 5
    # 28 Oct: the owner confirms the Surat shutdown; tonight's result is suppressed by it
    covered = _r("suppressed by Surat plant shutdown, 10–24 August")
    got = _night([covered], prev, D(2026, 10, 28))[open_["fingerprint"]]
    assert got["status"] == "covered" and got["change"] == "Covered"
    assert got["covered"] == {
        "date": "2026-10-28",
        "by": "Surat plant shutdown, 10–24 August",
        "open_from": "2026-10-23",
        "open_to": "2026-10-27",
    }
    c = findings.counts(list(_night([covered], prev, D(2026, 10, 28)).values()), [], 1)
    assert c["findings"] == 0 and c["resolved"] == 0 and c["suppressed"] == 1
    # carried while still covered, with the same history (the pattern counts it once)
    later = _night([covered], [got], D(2026, 11, 1))[open_["fingerprint"]]
    assert later["status"] == "covered" and later["covered"] == got["covered"]


def test_a_finding_merged_into_another_is_not_resolved():
    first = _night([_r()], [], D(2026, 10, 31))
    (f,) = first.values()
    merged = _r("also seen as net_revenue (net_hiding_gross)")
    got = _night([merged], list(first.values()), D(2026, 11, 1))[f["fingerprint"]]
    assert got["status"] == "merged" and got["merged"]["as"] == "net_revenue (net_hiding_gross)"
    assert findings.counts([got], [], 0)["resolved"] == 0


def test_cleared_by_data_and_an_answer_are_still_resolved():
    first = _night([_r()], [], D(2026, 10, 31))
    (f,) = first.values()
    gone = _night([_r("no signal", passed=False)], list(first.values()), D(2026, 11, 1))
    assert gone[f["fingerprint"]]["resolved"]["reason"] == "Cleared by data"
    paused = _r("explained by your answer · Skarfab paused until 1 Dec", passed=False)
    answered = _night([paused], list(first.values()), D(2026, 11, 1))
    assert answered[f["fingerprint"]]["resolved"]["reason"].startswith("Explained by your answer")


def test_a_goal_shortfall_is_tested_and_recorded_but_never_a_finding():
    goal = _r(slots={"kind": "goal", "goal": "fy27"}, series="goal|fy27", metric="net_revenue")
    goal.money = "28051441.00"
    other = _r()
    detect.bh([goal, other], Decimal("0.01"))
    assert goal.q_value is None  # outside the Sweep's FDR family
    goal.passed, goal.reason = detect._gate(goal, Decimal("500000"), _CTX)  # type: ignore[arg-type]
    assert not goal.passed and goal.reason == detect.GOAL_AT_RISK
    assert not _night([goal], [], D(2026, 11, 1))


_CTX = SimpleNamespace(
    spec={"detectors": {"fdr_q": "0.01"}, "suppressions": []}, holds={}, run_date=D(2026, 11, 1)
)


def test_a_week_that_did_not_pass_is_supporting_evidence_on_the_test_tab():
    from othor.web.sweep_view import supporting

    p1 = {
        "detector": "net_hiding_gross",
        "metric": "net_revenue",
        "breakdown": "region",
        "breakdown_value": None,
        "window": ["2026-09-01", "2026-10-31"],
    }
    week = {
        "detector": "forecast_breach",
        "metric": "net_revenue",
        "passed": False,
        "reason": "not significant after FDR (q 0.027)",
        "q_value": "0.027107803573",
        "money": "3578557.77",
        "direction": "down",
        "window": ["2026-10-12", "2026-10-18"],
        "slots": {
            "grain": "week",
            "period": "2026-10-12",
            "actual": "6100000",
            "lower": "9700000",
            "upper": "12100000",
        },
    }
    rv = SimpleNamespace(results=[week, {**week, "passed": True}])
    (line,) = supporting(rv, p1)  # type: ignore[arg-type]
    assert line.startswith("Forecast breach · week of 12 Oct: ₹0.61 Cr against its 80% band")
    assert "under" in line and "not significant on its own" in line
    assert supporting(rv, {**p1, "breakdown_value": "South"}) == []  # type: ignore[arg-type]


def test_a_finding_that_comes_back_within_a_week_keeps_its_history():
    """M15 (the 100-night check, 13 Nov 2026): a finding resolved one night (here it dipped under
    the materiality floor) that fires again while the Resolved tab still keeps it reopens as
    "Came back" with its first-seen date, never "New" with its history lost."""
    d1, d2, d3 = D(2026, 7, 29), D(2026, 7, 30), D(2026, 7, 31)
    n1 = _night([_r()], [], d1)
    (fp,) = n1
    assert n1[fp]["change"] == "New"
    n2 = _night([_r("below materiality", passed=False)], list(n1.values()), d2)
    assert n2[fp]["status"] == "resolved"
    n3 = _night([_r()], list(n2.values()), d3)
    back = n3[fp]
    assert back["status"] == "open" and back["change"] == "Came back"
    assert back["first_seen"]["date"] == d1.isoformat() and back["days_open"] == 3
    # the night after, it is simply open again, counting from its first night
    n4 = _night([_r()], list(n3.values()), D(2026, 8, 1))
    assert n4[fp]["change"] == "Open · day 4"


def test_a_finding_back_after_the_resolved_week_is_new_again():
    n1 = _night([_r()], [], D(2026, 7, 1))
    (fp,) = n1
    prev = list(_night([_r("no signal", passed=False)], list(n1.values()), D(2026, 7, 2)).values())
    for i in range(3, 12):  # the Resolved tab lets it go after 7 days
        prev = list(_night([], prev, D(2026, 7, i)).values())
    assert fp not in {f["fingerprint"] for f in prev}
    assert _night([_r()], prev, D(2026, 7, 12))[fp]["change"] == "New"


def test_a_finding_covered_by_a_pattern_comes_back_with_its_history():
    """The 100-night check (13 Nov 2026): open for weeks, then covered by a confirmed known
    pattern; when the pattern's window ends and it fires again it is "Came back", first seen as
    before, not "New"."""
    nights = [D(2026, 9, 20) + dt.timedelta(days=i) for i in range(3)]
    prev: list = []
    for day in nights:
        prev = list(_night([_r()], prev, day).values())
    (fp,) = [f["fingerprint"] for f in prev]
    covered = _r("suppressed by Diwali week", passed=False)
    prev = list(_night([covered], prev, D(2026, 9, 23)).values())
    assert prev[0]["status"] == "covered"
    back = _night([_r()], prev, D(2026, 9, 24))[fp]
    assert back["status"] == "open" and back["change"] == "Came back"
    assert back["first_seen"]["date"] == "2026-09-20" and back["days_open"] == 5
