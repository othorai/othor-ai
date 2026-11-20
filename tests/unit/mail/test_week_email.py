# SPDX-License-Identifier: Apache-2.0
"""0.28.5 · "Since last week" in the brief email, short: the line and what got fixed (simple and
full); a quiet day's email carries the line only. An issue without it renders as before."""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from othor.core.config import Settings
from othor.mail.messages import BriefMail, brief_email
from tests.unit.mail.world import issue_1nov, quiet_from

WEEK: dict[str, Any] = {
    "word": "last week",
    "from": "2026-10-26",
    "counts": {"reported": 3, "fixed": 1, "open": 4},
    "line": "Last 7 days: 3 reported · 1 fixed · 4 still open · longest open: South fell, day 9",
    "fixed": [
        {
            "fingerprint": "x",
            "title": "Ravira Exports stopped invoicing",
            "how": "Back to normal on 25 Nov",
            "date": "2026-11-25",
        }
    ],
    "open": [],
}


def _mail(body: dict[str, Any], fmt_: str = "simple") -> BriefMail:
    return BriefMail(
        company_name="Indigo Thread Co.",
        slug="indigothread",
        timezone="Asia/Kolkata",
        brief_name="Leadership",
        date=dt.date(2026, 11, 1),
        body=body,
        words={"title": body.get("title", ""), "line": body.get("line", ""), "situation": ""},
        email_format=fmt_,
        run_number=12,
        spec_version=3,
    )


def _render(body: dict[str, Any], fmt_: str = "simple") -> tuple[str, str]:
    m = brief_email(
        _mail(body, fmt_),
        to="owner@example.com",
        open_url="https://indigothread.othor.ai/brief",
        unsubscribe_url="https://indigothread.othor.ai/u/x",
        settings=Settings(_env_file=None, env="test"),  # type: ignore[call-arg]
    )
    return m.html, m.text


def test_the_line_and_the_fixed_items_in_both_formats() -> None:
    body = {**copy.deepcopy(issue_1nov()), "week": WEEK}
    for fmt_ in ("simple", "full"):
        html, text = _render(body, fmt_)
        for out in (html, text):
            assert "Since last week" in out and WEEK["line"] in out
            assert "Ravira Exports stopped invoicing" in out and "Back to normal on 25 Nov" in out
            # last in the issue, after Ask today, just above the button and the footer
            assert (
                out.index(body["ask"]["text"])
                < out.index(WEEK["line"])
                < out.index("Open the brief")
            )


def test_a_quiet_day_carries_the_line_only() -> None:
    body = {**quiet_from(copy.deepcopy(issue_1nov())), "week": WEEK}
    html, text = _render(body)
    for out in (html, text):
        assert WEEK["line"] in out and "Back to normal on 25 Nov" not in out


def test_an_issue_from_before_renders_as_before() -> None:
    body = copy.deepcopy(issue_1nov())
    body.pop("week", None)  # an issue stored before 0.28.5 has no week (the golden now has one)
    html, text = _render(body)
    assert "Since last week" not in html and "Since last week" not in text
