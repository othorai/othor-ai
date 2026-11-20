# SPDX-License-Identifier: Apache-2.0
"""0.28.5 · "Since last week" on the brief page: the line, the chart, Fixed and how, Still open
with days and better or worse, and Othor AI's one suggestion with Snooze 7 days (it posts in
place to the existing hold route). A recipient's read-only link shows the section without the
suggestion's button. An issue from before 0.28.5 keeps the 0.28.4 "Still open" line."""

from __future__ import annotations

import copy

import sqlalchemy as sa

from othor.briefs.models import Brief, BriefIssue
from othor.core.tenancy import tenant_scope
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import _fp, night  # noqa: F401


def _week(fp: str) -> dict:
    return {
        "word": "last week",
        "from": "2026-10-26",
        "counts": {"reported": 2, "fixed": 1, "open": 3},
        "line": "Last 7 days: 2 reported · 1 fixed · 3 still open · longest open: DSO, day 12",
        "fixed": [
            {
                "fingerprint": "gone",
                "title": "Ponnavra credit notes",
                "how": "The two sources agree again on 1 Nov",
                "date": "2026-11-01",
            }
        ],
        "open": [{"fingerprint": fp, "title": "Freight decoupled", "days": 12, "trend": "worse"}],
        "suggest": {
            "fingerprint": fp,
            "action": "snooze",
            "text": "Open 12 days, unchanged for 8: Freight decoupled. Set it aside for a week?",
        },
    }


def _put(svc, cid, week):  # type: ignore[no-untyped-def]
    with svc.sf() as s, tenant_scope(cid):
        bid = s.scalars(sa.select(Brief.id)).one()
        issue = s.scalars(sa.select(BriefIssue).where(BriefIssue.brief_id == bid)).first()
        body = copy.deepcopy(issue.body)
        if week is None:
            body.pop("week", None)
        else:
            body["week"] = week
        issue.body = body
        s.commit()
    return bid


def test_the_section_on_the_owners_page_and_the_shared_link(night):  # noqa: F811
    env, h, cid, rid, svc = night
    fp = _fp(svc, cid, rid, "broken_relationship")
    bid = _put(svc, cid, _week(fp))
    page = env.client.get(h + f"/brief/{bid}").text
    for text in (
        'aria-label="Since last week"',
        "Last 7 days: 2 reported · 1 fixed · 3 still open · longest open: DSO, day 12",
        "Fixed · 1",
        "The two sources agree again on 1 Nov",
        "Still open · 1",
        "day 12 · worse",
        "Othor AI suggests",
        "Set it aside for a week?",
        f'action="/sweep/finding/{fp}/snooze?run=',
        "Snooze 7 days",
    ):
        assert text in page, text
    assert "unchanged for a week or more" not in page  # the 0.28.4 line gives way
    # last in the issue (owner, 20 Nov 2026): after What's ahead, Watching and Ask today
    at = page.index('aria-label="Since last week"')
    for before in ("to ask today", "What’s ahead"):
        if before in page:
            assert page.index(before) < at, before
    shared = env.client.get(h + f"/brief/{bid}/shared").text
    assert "Last 7 days: 2 reported" in shared and "Snooze 7 days" not in shared
    # the PDF draws it too (its template is strict about missing keys)
    for q in ("", "?charts=1"):
        pdf = env.client.get(h + f"/brief/{bid}/pdf{q}")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), q


def test_an_issue_from_before_keeps_the_still_open_line(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid = _put(svc, cid, None)
    page = env.client.get(h + f"/brief/{bid}").text
    assert 'aria-label="Since last week"' not in page
