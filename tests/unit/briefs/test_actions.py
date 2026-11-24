# SPDX-License-Identifier: Apache-2.0
"""What to do (Morning Brief 2.5, #110; plan § A): the actions are picked and worded by code from
the issue's own items, goals and watches. An off-track goal first, then the items in rank, then a
breached watch no item covers; a finding named in a higher action joins it; at most three; every
number in a sentence is one the item, goal or watch already holds (rule 1); one template per
check, so a brief has its actions without a model (rule 4)."""

from __future__ import annotations

import re

import pytest

from othor.briefs import actions

SPEC = {
    "metrics": {
        "net_revenue": {"name": "Net revenue", "unit": "inr"},
        "dso": {"name": "DSO", "unit": "days"},
        "freight": {"name": "Freight expense", "unit": "inr"},
    },
}


def _f(fp, detector, title, *, entities=(), slots=None, question_to=None, metric="net_revenue"):
    q = {"text": "Q?", "to": [{"name": question_to, "role": "Head"}]} if question_to else {}
    return {
        "fingerprint": fp,
        "detector": detector,
        "metric": metric,
        "entities": list(entities),
        "slots": slots or {},
        "text": {"title": title, "question": q},
    }


def _item(f, change="Open · day 2", money="₹1.4 Cr", consequence="November close in 29 days"):
    return {
        "fingerprint": f["fingerprint"],
        "detector": f["detector"],
        "metric": f["metric"],
        "title": f["text"]["title"],
        "change": change,
        "money": money,
        "consequence": {"text": consequence},
    }


SOUTH = _f(
    "a1",
    "net_hiding_gross",
    "South fell ₹1.4 Cr; West’s rise hid it",
    entities=["South", "West"],
    slots={
        "fall": {"name": "South"},
        "drill": {
            "share": "0.72",
            "accounts": [
                {"name": "Tessora EU"},
                {"name": "Skarfab", "paused": {"until": "2026-12-01"}},
                {"name": "Ravira Exports"},
            ],
        },
    },
    question_to="Leela Tandon",
)
RAVIRA = _f(
    "b2",
    "missing_records",
    "Ravira Exports stopped invoicing on 9 Oct",
    entities=["Ravira Exports"],
)
SILENT = _f(
    "c3", "missing_records", "14 customers still marked active haven’t invoiced in 90+ days"
)
FREIGHT = _f(
    "d4",
    "broken_relationship",
    "Freight expense tracked metres dispatched for 20 weeks, then decoupled on 14 Sep",
    entities=["freight", "metres"],
    metric="freight",
)
GOAL = {
    "key": "fy_rev",
    "metric": "net_revenue",
    "target": "600000000",
    "projection": "572000000",
    "by": "2027-03-31",
    "direction": "at_least",
}
GOAL_LINE = {
    "key": "fy_rev",
    "text": "Net revenue FY27 · ₹60 Cr target · tracking ₹57.2 Cr",
    "tone": "off",
}
ASK = {
    "text": "Have Tessora EU and Ravira Exports moved orders elsewhere?",
    "to": "Priya Menon",
    "role": "Head of Sales",
}


def _build(items_from, goals=(), lines=(), watches=(), ask=ASK):
    fs = {f["fingerprint"]: f for f in items_from}
    return actions.build(
        SPEC,
        {},
        items=[_item(f) for f in items_from],
        findings=fs,
        goals=list(goals),
        goal_lines=list(lines),
        watches=list(watches),
        ask=ask,
    )


def test_an_off_track_goal_comes_first_with_its_gap_computed_by_code():
    got = _build([SOUTH, SILENT], goals=[GOAL], lines=[GOAL_LINE])
    assert [a["kind"] for a in got] == ["goal", "finding", "finding"]
    g = got[0]
    assert g["text"] == (
        "Find ₹2.8 Cr more net revenue by 31 Mar: at today’s pace it ends at ₹57.2 Cr "
        "against the ₹60 Cr goal."
    )
    assert g["money"] == "₹2.8 Cr" and g["status"] == "Off track" and g["fingerprints"] == []


def test_a_goal_on_track_or_at_risk_is_no_action():
    got = _build([SOUTH], goals=[GOAL], lines=[{**GOAL_LINE, "tone": "risk"}])
    assert [a["kind"] for a in got] == ["finding"]


def test_a_finding_named_in_a_higher_action_joins_it():
    got = _build([SOUTH, RAVIRA, SILENT])
    assert len(got) == 2
    south = got[0]
    assert south["fingerprints"] == ["a1", "b2"]
    assert [w["title"] for w in south["why"]] == [SOUTH["text"]["title"], RAVIRA["text"]["title"]]
    # the top item asks the brief's own person (one source for the panel and the brief)
    assert south["who"] == {"name": "Priya Menon", "role": "Head of Sales"}
    assert south["text"].startswith(
        "Call Tessora EU and Ravira Exports: they carry most of South’s ₹1.4 Cr fall"
    )
    assert "(Skarfab is on a known pause)" in south["text"]


def test_a_pair_of_numbers_never_joins_by_name():
    pair = _f("e5", "lead_lag", "Orders move 3 weeks before net revenue", entities=["South"])
    got = _build([SOUTH, pair])
    assert [a["fingerprints"] for a in got] == [["a1"], ["e5"]]


def test_at_most_three_actions():
    many = [
        SOUTH,
        SILENT,
        FREIGHT,
        _f("f6", "changepoint", "DSO stepped up in the week of 7 Sep", metric="dso"),
    ]
    got = _build(many, goals=[GOAL], lines=[GOAL_LINE])
    assert len(got) == actions.MAX == 3


def test_a_breached_watch_on_a_number_no_item_covers():
    w = {
        "id": "w1",
        "label": "Receivables over 60 days, share",
        "value": "0.13",
        "threshold": "0.12",
        "direction": "above",
        "owner_name": "Leela Tandon",
        "status": "breached",
        "indicator": {"kind": "value", "metric": "receivables_over_60"},
    }
    same = {**w, "id": "w2", "indicator": {"kind": "value", "metric": "net_revenue"}}
    within = {**w, "id": "w3", "status": "within"}
    got = _build([SOUTH], watches=[w, same, within])
    assert [a["key"] for a in got] == ["finding:a1", "watch:w1"]
    # 0.33.1: the number's name in lower case mid-sentence, "the limit you set"
    assert got[1]["text"] == (
        "Ask Leela Tandon why receivables over 60 days, share reads 13%, above the 12% limit "
        "you set."
    )


@pytest.mark.parametrize("detector", sorted(actions.LEADS))
def test_every_check_has_a_template_and_quotes_only_the_findings_numbers(detector):
    title = "Something moved ₹3.6 Cr in the week of 7 Sep, up 32%"
    f = _f("z9", detector, title)
    text = actions.finding_text(f)
    # 0.33.1: no capital after the colon; only the title's numbers
    assert not re.search(r": [A-Z]", text)
    numbers = re.findall(r"\d[\d,.]*", text)
    assert numbers and all(n in title for n in numbers)


def test_an_unknown_check_still_gets_a_sentence():
    assert (
        actions.finding_text(_f("z", "outside_signal", "Diesel rose 4%"))
        == "Look into it: diesel rose 4%."
    )


# --- 0.33.1 (the 100-night run and the live rerun, 22 Nov 2026) ------------------------------


def test_a_name_after_the_colon_keeps_its_capital():
    f = _f("n1", "concentration", "Four customers now hold 58% of the order book")
    assert actions.finding_text(f) == (
        "Win orders beyond your biggest customers: four customers now hold 58% of the order book."
    )
    g = _f("n2", "cross_source", "Tessora EU differs by ₹2 L", entities=["Tessora EU"])
    assert actions.finding_text(g) == "Reconcile the two sources: Tessora EU differs by ₹2 L."
    h = _f("n3", "changepoint", "DSO stepped up in the week of 5 Oct", metric="dso")
    assert (
        actions.finding_text(h)
        == "Find out what changed in the week of 5 Oct, when DSO stepped up."
    )


def test_many_silent_customers_are_named_before_they_are_called():
    assert actions.finding_text(SILENT) == (
        "Have someone call the 14 customers still marked active who haven’t invoiced in 90+ days."
    )


def test_one_silent_customer_is_not_named_twice():
    assert actions.finding_text(RAVIRA) == (
        "Call Ravira Exports to find out why they stopped invoicing on 9 Oct."
    )


def test_net_hiding_gross_does_not_repeat_its_title():
    text = actions.finding_text(SOUTH)
    assert text == (
        "Call Tessora EU and Ravira Exports: they carry most of South’s ₹1.4 Cr fall, which "
        "West’s rise hid (Skarfab is on a known pause)."
    )
    assert text.count("South") == 1


def test_a_step_says_when_it_changed():
    f = _f("s1", "changepoint", "Receivables over 60 days stepped up in the week of 10 Aug")
    assert actions.finding_text(f) == (
        "Find out what changed in the week of 10 Aug, when receivables over 60 days stepped up."
    )


def test_an_uneven_spread_asks_what_changed_there():
    f = _f(
        "r1",
        "rising_variance",
        "Dispatch time spread at Coimbatore tripled from 7 Sep while the average held at 4.3 days",
        entities=["Coimbatore"],
        metric="dispatch_time",
    )
    assert actions.finding_text(f) == (
        "Ask what changed at Coimbatore from 7 Sep: the spread of dispatch time tripled while "
        "the average held at 4.3 days."
    )


W_REC = {
    "id": "w1",
    "label": "Receivables over 60 days, share",
    "value": "0.1204",
    "threshold": "0.12",
    "direction": "above",
    "owner_name": "Leela Tandon",
    "status": "breached",
    "indicator": {"kind": "value", "metric": "receivables_over_60"},
}
SPEC_DT = {
    **SPEC,
    "metrics": {**SPEC["metrics"], "dispatch_time": {"name": "Dispatch time", "unit": "days"}},
}


def _watch_only(w, spec=SPEC, **kw):
    return actions.build(
        spec, {}, items=[], findings={}, goals=[], goal_lines=[], watches=[w], ask=None, **kw
    )


def test_a_watch_never_reads_above_a_limit_it_equals():
    got = _watch_only(W_REC)
    assert got[0]["text"] == (
        "Ask Leela Tandon why receivables over 60 days, share reads 12.04%, above the 12% limit "
        "you set."
    )


def test_a_watch_sentence_names_its_owner_once():
    assert _watch_only(W_REC)[0]["who"] is None  # the sentence already says who to ask


def test_a_watch_in_days_reads_in_days():
    w = {
        **W_REC,
        "id": "w9",
        "label": "Dispatch time spread, Coimbatore · 7 days",
        "value": "2.164295",
        "threshold": "1.6",
        "owner_name": "Aditya Naik",
        "indicator": {"kind": "spread", "metric": "dispatch_time", "breakdown_value": "Coimbatore"},
    }
    assert _watch_only(w, SPEC_DT)[0]["text"] == (
        "Ask Aditya Naik why dispatch time spread, Coimbatore reads 2.2 days, above the 1.6 days "
        "limit you set."
    )


def test_watches_off_the_briefs_numbers_are_left_out():
    """The live Dispatch brief (indigodemo, run #10) offered receivables and customer watches."""
    w = {**W_REC, "value": "0.16"}
    keep = lambda x: (x.get("indicator") or {}).get("metric") == "dispatch_time"  # noqa: E731
    assert _watch_only(w, watch_ok=keep) == []


def test_a_watch_on_an_items_number_folds_into_that_item():
    spread = _f(
        "r1",
        "rising_variance",
        "Dispatch time spread at Coimbatore tripled from 7 Sep",
        entities=["Coimbatore"],
        metric="dispatch_time",
    )
    w = {
        **W_REC,
        "id": "w9",
        "value": "2.2",
        "threshold": "1.6",
        "indicator": {"kind": "spread", "metric": "dispatch_time", "breakdown_value": "Coimbatore"},
    }
    got = _build([spread], watches=[w])
    assert [a["key"] for a in got] == ["finding:r1"]


def test_a_quiet_night_offers_its_largest_still_open_finding():
    still = [{"fingerprint": "c3", "title": SILENT["text"]["title"], "days": 12}]
    got = actions.build(
        SPEC,
        {},
        items=[],
        findings={"c3": SILENT},
        goals=[GOAL],
        goal_lines=[GOAL_LINE],
        watches=[],
        ask=None,
        still_open=still,
    )
    assert [a["key"] for a in got] == ["goal:fy_rev", "finding:c3"]
    assert got[1]["status"] == "Still open · 12 days"


def test_still_open_findings_wait_while_the_brief_has_items():
    still = [{"fingerprint": "c3", "title": SILENT["text"]["title"], "days": 12}]
    got = actions.build(
        SPEC,
        {},
        items=[_item(SOUTH)],
        findings={"a1": SOUTH, "c3": SILENT},
        goals=[],
        goal_lines=[],
        watches=[],
        ask=ASK,
        still_open=still,
    )
    assert [a["key"] for a in got] == ["finding:a1"]


def test_the_same_inputs_give_the_same_actions():
    a = _build([SOUTH, RAVIRA, SILENT], goals=[GOAL], lines=[GOAL_LINE])
    b = _build([SOUTH, RAVIRA, SILENT], goals=[GOAL], lines=[GOAL_LINE])
    assert a == b


def test_a_focused_briefs_watches_are_its_own_numbers():
    """scope.watch_in_brief: the Dispatch brief keeps a dispatch watch, not a receivables one."""
    from othor.briefs import scope

    spec = {
        "metrics": {
            "dispatch_time": {"name": "Dispatch time", "unit": "days"},
            "receivables_over_60": {"name": "Receivables over 60 days", "unit": "inr"},
        }
    }
    version = {"headline": ["dispatch_time"], "scope": {}}
    disp = {**W_REC, "indicator": {"kind": "spread", "metric": "dispatch_time"}}
    assert scope.watch_in_brief(spec, version, disp)
    assert not scope.watch_in_brief(spec, version, W_REC)
    # the default brief keeps the whole company
    assert scope.watch_in_brief(spec, {"default": True, "headline": ["dispatch_time"]}, W_REC)
