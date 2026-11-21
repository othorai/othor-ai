# SPDX-License-Identifier: Apache-2.0
"""Home's brief cards with a shared lead (owner, 21 Nov 2026, indigodemo: Leadership and South vs
West both led with "South fell ₹1.49 Cr…"): the later card says "Same lead as Leadership" and shows
its own next item; the brief itself keeps its order. Needs `make dev && make seed`."""

from __future__ import annotations

import html

import sqlalchemy as sa

from othor.briefs import service
from othor.briefs.models import BriefIssue
from othor.core.tenancy import tenant_scope
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def test_a_second_card_with_the_same_lead_shows_its_next_item(night):  # noqa: F811
    env, h, cid, rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        lead = service.active(s)[0]
        issue = s.scalars(
            sa.select(BriefIssue).where(BriefIssue.brief_id == lead.id, BriefIssue.run_id == rid)
        ).one()
        items = issue.body.get("items") or []
        assert len(items) > 1
        other = service.create(
            s, {**service.current(s, lead).body, "name": "South vs West"}, actor="test"
        )
        s.add(
            BriefIssue(
                brief_id=other.id,
                brief_version=1,
                run_id=rid,
                date=issue.date,
                status="ready",
                title=issue.title,
                quiet=False,
                body=issue.body,
                issue_hash=issue.issue_hash,
            )
        )
        s.commit()
        first_title, second_title = issue.title, str(items[1]["title"])
    page = env.client.get(h + "/home").text
    assert "Same lead as Leadership" in page
    assert html.escape(second_title, quote=False)[:40] in page
    assert page.count(html.escape(first_title, quote=False)[:40]) >= 1
    # the brief itself keeps its order
    assert (
        html.escape(first_title, quote=False)[:40] in env.client.get(h + f"/brief/{other.id}").text
    )
