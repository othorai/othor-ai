# SPDX-License-Identifier: Apache-2.0
"""The brief email's subject (M15.7d, owner 14 Nov 2026): it leads with the highest-ranked item
that changed (New, Worse, Came back; for a Weekly or Monthly issue, since its last issue), says
"Still open · …" when nothing did, and always ends with the short date, so each morning is its own
email. The preheader counts what changed. Every word is from the stored issue."""

from __future__ import annotations

import datetime as dt

from othor.mail.messages import BriefMail, brief_preheader, brief_subject

SAT = dt.date(2026, 11, 14)


def _item(rank: int, title: str, change: str) -> dict:
    return {"rank": rank, "title": title, "change": change}


def _mail(items: list[dict], fmt_: str = "simple", **body) -> BriefMail:
    return BriefMail(
        company_name="Indigo Thread Co.",
        slug="indigothread",
        timezone="Asia/Kolkata",
        brief_name="Leadership",
        date=SAT,
        body={
            "quiet": not items,
            "items": items,
            "tiles": [],
            "ask": {"to": "Priya Menon"},
            **body,
        },
        words={},
        email_format=fmt_,
        run_number=12,
        spec_version=3,
    )


SOUTH = "South fell ₹1.35 Cr; West’s rise hid it"
DISPATCH = "Dispatch time spread at Coimbatore tripled"


def test_nothing_changed_says_still_open_with_the_date() -> None:
    m = _mail([_item(1, SOUTH, "Open · day 3"), _item(2, DISPATCH, "Open · day 3")])
    # 0.28.5: with how long the top item has been open
    assert brief_subject(m) == f"Still open, day 3 · {SOUTH} · Leadership · Sat 14 Nov"
    assert brief_preheader(m) == "2 things need you · ask Priya one question today"


def test_the_highest_ranked_change_leads() -> None:
    m = _mail([_item(1, SOUTH, "Open · day 3"), _item(2, DISPATCH, "New")])
    assert brief_subject(m) == f"{DISPATCH} · Leadership · Sat 14 Nov"
    assert brief_preheader(m) == "1 new · 2 things need you · ask Priya one question today"
    worse = _mail([_item(1, SOUTH, "Worse"), _item(2, DISPATCH, "New")])
    assert brief_subject(worse).startswith(SOUTH)
    assert brief_preheader(worse).startswith("1 new · 1 worse · 2 things need you")
    back = _mail([_item(1, SOUTH, "Open · day 9"), _item(2, DISPATCH, "Came back")])
    assert brief_subject(back).startswith(DISPATCH)
    assert brief_preheader(back).startswith("1 came back · 2 things need you")


def test_the_full_format_reads_the_same_way() -> None:
    m = _mail([_item(1, SOUTH, "New")], fmt_="full")
    assert brief_subject(m) == f"{SOUTH} · Leadership · Sat 14 Nov"
    assert brief_preheader(m).startswith("1 new · 1 item")


def test_a_quiet_day_has_the_date_too() -> None:
    assert brief_subject(_mail([])) == "Quiet day · Leadership · Sat 14 Nov"


def test_a_weekly_issue_leads_with_what_changed_in_its_week() -> None:
    # the issue's tags are already against its last issue (M15.7c); the subject reads them
    m = _mail(
        [_item(1, SOUTH, "Open · day 9")],
        period={"from": "2026-11-07", "word": "last week"},
    )
    assert brief_subject(m) == f"Still open, day 9 · {SOUTH} · Leadership · Sat 14 Nov"
