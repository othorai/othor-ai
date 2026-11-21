# SPDX-License-Identifier: Apache-2.0
"""The brief's words (docs/04 §9, 4h1): templates from computed values, each clause only when the
data makes it true; the title and Ask today checks run as code before the brief is made."""

from __future__ import annotations

from othor.briefs import title


def _p1(drill=None):
    slots = {
        "fall": {"name": "South", "change": "-14000001.60"},
        "rise": {"name": "West", "change": "12100000.00"},
        "net": "-1900000.00",
        "period": "2026-10-01",
        "previous": "2026-09-01",
    }
    if drill:
        slots["drill"] = drill
    return {
        "fingerprint": "p1",
        "detector": "net_hiding_gross",
        "metric": "net_revenue",
        "state": "assessed",
        "confidence": "high",
        "money": "14000001.60",
        "slots": slots,
        "text": {
            "title": "South fell ₹1.4 Cr; West’s rise hid it",
            "line": "Net revenue moved −₹0.19 Cr from September to October.",
            "question": {
                "text": "What changed for South customers in October?",
                "to": [{"name": "Leela Tandon", "role": "Head of Finance"}],
            },
        },
    }


DRILL = {
    "value": "South",
    "accounts": [
        {"name": "Ravira Exports", "change": "-5400000", "last": "2026-10-09"},
        {"name": "Tessora EU", "change": "-3300000", "last": "2026-10-08"},
        {"name": "Skarfab", "change": "-1100000", "last": "2026-10-06"},
    ],
    "share": "0.70",
    "fall": "-14000001.60",
    "to": "2026-10-31",
}


def test_p1_title_has_its_clauses_only_when_true():
    t = title.headline([_p1(DRILL)])
    assert t == (
        "South fell ₹1.4 Cr while West’s growth hid it; three accounts carry 70% of the drop "
        "and none has invoiced since 9 Oct."
    )
    # one account invoiced after the 9th: the 'none has invoiced' clause is dropped
    late = {
        **DRILL,
        "accounts": [*DRILL["accounts"][:2], {**DRILL["accounts"][2], "last": "2026-10-20"}],
    }
    assert title.headline([_p1(late)]).endswith("three accounts carry 70% of the drop.")
    # under 70% explained: the title says what is measured, the rest is not determinable
    low = {**DRILL, "share": "0.55"}
    assert title.headline([_p1(low)]).endswith(
        "three accounts carry 55% of the drop; the rest is not determinable from current sources."
    )
    assert title.headline([_p1()]) == "South fell ₹1.4 Cr while West’s growth hid it."


def test_the_title_check_rejects_the_boards_bad_examples():
    items = [_p1(DRILL)]
    assert title.check_title("Leadership brief · week 44", items) == ["label"]
    assert title.check_title("Revenue is under the forecast band", items) == ["label"]
    assert title.check_title("Revenue is 8% under band", items) == ["no position"]
    assert title.check_title(title.headline(items), items) == []


def test_the_ask_today_check():
    ok = {
        "text": "Have Ravira Exports, Tessora EU and Skarfab moved orders elsewhere?",
        "to": "Priya Menon",
    }
    assert title.check_ask(ok) == []
    assert title.check_ask({"text": "How are we tracking on South?", "to": None}) == ["no owner"]
    assert title.check_ask(
        {"text": "Review receivables, order book and pricing", "to": "Leela Tandon"}
    ) == ["not a question"]
    assert title.check_ask({"text": "Why? And what next?", "to": "Leela Tandon"}) == [
        "more than one"
    ]


def test_ask_today_from_the_drill_goes_to_the_owner_of_customers():
    owners = [
        {"name": "Leela Tandon", "role": "Head of Finance", "areas": ["net_revenue"]},
        {"name": "Priya Menon", "role": "Head of Sales", "areas": ["customer", "order_book"]},
    ]
    got = title.ask_today([_p1(DRILL)], owners)
    assert got == {
        "text": (
            "Have Ravira Exports, Tessora EU and Skarfab moved orders elsewhere, "
            "or are they on hold?"
        ),
        "to": "Priya Menon",
        "role": "Head of Sales",
    }
    plain = title.ask_today([_p1()], owners)
    assert plain["text"] == "What changed for South customers in October?"
    assert plain["to"] == "Leela Tandon"
    assert title.ask_today([], owners) is None


def test_the_line_names_what_is_on_plan():
    tiles = [
        {
            "label": "Net revenue · Oct",
            "metric": "net_revenue",
            "comparison": {"kind": "band", "text": "1% under band"},
        },
        {
            "label": "Gross margin per metre · week of 19 Oct",
            "metric": "gross_margin_per_metre",
            "comparison": {"kind": "band", "text": "in band"},
        },
        {
            "label": "DSO · Oct",
            "metric": "dso",
            "comparison": {"kind": "goal", "text": "on track for 45 · vs target"},
        },
    ]
    names = {
        "gross_margin_per_metre": "Gross margin per metre",
        "dso": "DSO",
        "net_revenue": "Net revenue",
    }
    assert title.line(tiles, names) == "Gross margin per metre and DSO are on plan."
    assert title.quiet_title(tiles[1:], names) == (
        "Gross margin per metre and DSO are on plan; nothing significant came up."
    )


def test_a_known_pause_is_named_next_to_the_split_and_left_out_of_ask_today():
    paused = {
        **DRILL,
        "accounts": [
            *DRILL["accounts"][:2],
            {
                **DRILL["accounts"][2],
                "paused": {"until": "2026-12-01", "answered_on": "2026-10-30"},
            },
        ],
    }
    t = title.headline([_p1(paused)])
    assert t == (
        "South fell ₹1.4 Cr while West’s growth hid it; three accounts carry 70% of the drop "
        "and none has invoiced since 9 Oct; one is Skarfab’s known pause to 1 Dec."
    )
    assert title.pause_sentence(paused) == (
        "Skarfab is on a known pause to 1 Dec (your answer, 30 Oct)."
    )
    owners = [{"name": "Priya Menon", "role": "Head of Sales", "areas": ["customer"]}]
    ask = title.ask_today([_p1(paused)], owners)
    assert ask["text"] == (
        "Have Ravira Exports and Tessora EU moved orders elsewhere, or are they on hold?"
    )
    assert "Skarfab" not in ask["text"]


def _aov(change):
    return {
        "fingerprint": "aov",
        "detector": "changepoint",
        "metric": "aov",
        "money": "588000",
        "change": change,
        "days_open": 2,
        "text": {"title": "Average order value stepped up in the week of 12 Oct"},
    }


def test_the_title_leads_with_what_changed_tonight():
    # owner, 16 Nov 2026: the same top finding titled every brief for five days. What changed
    # leads, as in the email subject (M15.7d); nothing changed says how long it has been open.
    top = {**_p1(), "change": "Open · day 5", "days_open": 5}
    assert title.lead_headline([top, _aov("Worse")]) == (
        "Average order value stepped up in the week of 12 Oct."
    )
    assert title.lead_headline([top, _aov("Came back")]).startswith("Average order value")
    assert title.lead_headline([top, _aov("Open · day 2")]) == (
        "Still open, day 5: South fell ₹1.4 Cr while West’s growth hid it."
    )
    # the first brief: everything is new, so the top item leads as before
    assert title.lead_headline([{**_p1(), "change": "New"}]) == title.headline([_p1()])
    # Better is not news for the title (it goes down the list)
    assert title.lead_headline([top, _aov("Better")]).startswith("Still open, day 5")
    assert title.lead_headline([]) == ""
