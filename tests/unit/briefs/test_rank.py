# SPDX-License-Identifier: Apache-2.0
"""Ranking (docs/04 §8): score = money × urgency × state weight, in one function.

Urgency from the consequence date: 1.0 within 30 days, 0.6 within 90, 0.3 beyond (or no date).
State weight: Measured 1.0, Assessed High 1.0, Medium 0.7, Not determinable 0.8. Ties: newer
first. Needs confirmation never enters; up to 4 items; an item from a stale source is never first.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from othor.briefs import consequence, rank

RUN = dt.date(2026, 11, 1)


def _f(fp, money, state="assessed", conf="high", detector="net_hiding_gross", **kw):
    return {
        "fingerprint": fp,
        "status": "open",
        "detector": detector,
        "metric": kw.get("metric", "net_revenue"),
        "family": kw.get("family", "net_revenue"),
        "state": state,
        "confidence": conf,
        "money": str(money),
        "first_seen": {"date": kw.get("first", "2026-11-01")},
        "stale": kw.get("stale", False),
        "slots": kw.get("slots", {}),
        "text": {"consequence": kw.get("text", "")},
        "window": kw.get("window", ["2026-09-01", "2026-10-31"]),
    }


SPEC = {
    "metrics": {
        "net_revenue": {"name": "Net revenue", "family": "net_revenue"},
        "dso": {"name": "DSO", "components": ["net_revenue", "receivables_open"]},
        "receivables_over_60": {"name": "Receivables over 60 days", "family": "receivables"},
        "receivables_open": {"name": "Receivables", "family": "receivables"},
        "order_book": {"name": "Order book", "family": "orders"},
    },
    "forecast": {
        "goals": [
            {
                "key": "dso_under_45",
                "name": "DSO under 45 days by 31 Mar",
                "metric": "dso",
                "by": "2027-03-31",
            }
        ]
    },
}


def test_the_weights():
    assert rank.urgency(RUN, RUN + dt.timedelta(days=29)) == Decimal("1.0")
    assert rank.urgency(RUN, RUN + dt.timedelta(days=30)) == Decimal("1.0")
    assert rank.urgency(RUN, RUN + dt.timedelta(days=31)) == Decimal("0.6")
    assert rank.urgency(RUN, RUN + dt.timedelta(days=91)) == Decimal("0.3")
    assert rank.urgency(RUN, None) == Decimal("0.3")
    w = rank.state_weight
    assert w({"state": "measured"}) == Decimal("1.0")
    assert w({"state": "assessed", "confidence": "high"}) == Decimal("1.0")
    assert w({"state": "assessed", "confidence": "medium"}) == Decimal("0.7")
    assert w({"state": "not_determinable"}) == Decimal("0.8")


def test_consequence_dates_per_detector():
    nhg = consequence.of(_f("a", 1), SPEC, RUN)
    assert nhg == {"text": "November close in 29 days", "date": "2026-11-30"}
    conc = consequence.of(_f("b", 1, detector="concentration", metric="order_book"), SPEC, RUN)
    assert conc == {"text": "Next 60 days of revenue", "date": "2026-12-31"}
    # a goal on a metric built from this one (DSO from receivables): the goal's date
    rec = _f("c", 1, "measured", None, "changepoint", metric="receivables_over_60")
    rec["family"] = "receivables"
    got = consequence.of(rec, SPEC, RUN)
    assert got == {"text": "DSO under 45 days by 31 Mar", "date": "2027-03-31"}
    # no date: the detector's own words, ranked as beyond 90 days
    nd = _f("d", 1, "not_determinable", None, "missing_records", text="needs a call")
    assert consequence.of(nd, SPEC, RUN) == {"text": "Needs a call", "date": None}


def test_score_orders_by_money_urgency_and_state():
    a = _f("a", 14_000_000)  # month close: 1.0 × 1.0
    b = _f("b", 45_900_000, detector="rising_variance", metric="dispatch_time", text="x")
    c = _f("c", 9_150_000, conf="medium", detector="concentration", metric="order_book")
    got = rank.rank([b, c, a], SPEC, RUN)
    assert [x["fingerprint"] for x in got] == ["a", "b", "c"]  # 1.4 · 4.59 × 0.3 = 1.377
    a2 = _f("a", 14_000_000)
    assert rank.score(a2, SPEC, RUN) == Decimal("14000000.0")


def test_needs_confirmation_never_enters_and_four_at_most():
    fs = [_f(str(i), 10_000_000 - i) for i in range(6)]
    fs.append(_f("nc", 99_000_000, "needs_confirmation", None, "cross_source"))
    got = rank.rank(fs, SPEC, RUN)
    assert len(got) == 4 and "nc" not in {x["fingerprint"] for x in got}


def test_ties_go_to_the_newer_finding_and_stale_is_never_first():
    old = _f("old", 1_000_000, first="2026-10-20")
    new = _f("new", 1_000_000, first="2026-10-30")
    assert [x["fingerprint"] for x in rank.rank([old, new], SPEC, RUN)] == ["new", "old"]
    stale = _f("stale", 90_000_000, stale=True)
    got = rank.rank([stale, old], SPEC, RUN)
    assert [x["fingerprint"] for x in got] == ["old", "stale"]


# --- decided 6 Nov 2026: a tie-break within 10%, and a stable order across days -----------------


def test_within_ten_percent_measured_comes_first_then_the_earlier_date_then_money():
    p1 = _f("p1", 14_000_000)  # 1.40 Cr, month close (30 Nov)
    p7 = _f("p7", 45_900_000, detector="rising_variance", metric="dispatch_time", text="x")
    # 1.377 is within 10% of 1.40: both Assessed, P1's consequence date is the earlier
    assert [x["fingerprint"] for x in rank.rank([p7, p1], SPEC, RUN)] == ["p1", "p7"]
    # a Measured item within 10% goes first, whatever the score order
    m = _f("m", 13_000_000, "measured", None, "broken_relationship", metric="net_revenue")
    m["text"] = {"consequence": "x"}
    got = [x["fingerprint"] for x in rank.rank([p1, m], SPEC, RUN)]
    assert got == ["m", "p1"]
    # outside 10%: the score decides
    small = _f("small", 1_000_000, "measured", None, "changepoint")
    assert [x["fingerprint"] for x in rank.rank([small, p1], SPEC, RUN)] == ["p1", "small"]
    # same state, same date: the larger money
    a = _f("a", 10_000_000)
    b = _f("b", 9_500_000)
    assert [x["fingerprint"] for x in rank.rank([b, a], SPEC, RUN)] == ["a", "b"]


def test_an_item_in_yesterdays_brief_keeps_its_place_unless_its_score_moved():
    a, b, c = _f("a", 20_000_000), _f("b", 10_000_000), _f("c", 9_000_000)
    yesterday = [{"fingerprint": "c", "rank": 1, "score": "9000000.00"}]
    # c led yesterday and its score hasn't moved: it keeps rank 1
    got = rank.rank([a, b, c], SPEC, RUN, previous=yesterday)
    assert [x["fingerprint"] for x in got] == ["c", "a", "b"]
    # its score moved by more than 10%: it is ranked afresh
    moved = [{"fingerprint": "c", "rank": 1, "score": "7000000.00"}]
    got = rank.rank([a, b, c], SPEC, RUN, previous=moved)
    assert [x["fingerprint"] for x in got] == ["a", "b", "c"]


def test_yesterdays_place_past_todays_shorter_list_is_ranked_afresh():
    """Prod, 13 Nov 2026 (indigo2's night failed in its brief step: IndexError, pop from empty
    list): yesterday c was 3rd; tonight only two findings qualify, so place 3 doesn't exist.
    c takes its ranked place among today's items, and the list never runs short."""
    a, c = _f("a", 20_000_000), _f("c", 9_000_000)
    yesterday = [{"fingerprint": "c", "rank": 3, "score": "9000000.00"}]
    got = rank.rank([a, c], SPEC, RUN, previous=yesterday)
    assert [x["fingerprint"] for x in got] == ["a", "c"]
    # one item tonight, yesterday's 2nd: still just that item
    assert [
        x["fingerprint"]
        for x in rank.rank(
            [c], SPEC, RUN, previous=[{"fingerprint": "c", "rank": 2, "score": "9000000.00"}]
        )
    ] == ["c"]
    # a place that does exist tonight is still kept
    yesterday = [{"fingerprint": "c", "rank": 1, "score": "9000000.00"}]
    assert [x["fingerprint"] for x in rank.rank([a, c], SPEC, RUN, previous=yesterday)] == [
        "c",
        "a",
    ]


def test_a_finding_added_to_a_brief_is_one_of_its_items_in_its_ranked_place():
    from othor.briefs.assemble import with_pinned
    from othor.engine.findings import pattern_of

    fs = [_f(str(i), 10_000_000 - i * 1_000_000) for i in range(5)]
    for f in fs:
        f.update(series=[f"m{f['fingerprint']}|month||"], entities=[])
    small = fs[4]
    ranked = rank.rank(fs, SPEC, RUN)
    assert small not in ranked
    got = with_pinned(ranked, fs, SPEC, RUN, [pattern_of(small)])
    assert [f["fingerprint"] for f in got] == ["0", "1", "2", "4"]  # item 4 makes room
    assert with_pinned(ranked, fs, SPEC, RUN, []) == ranked
