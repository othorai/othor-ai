# SPDX-License-Identifier: Apache-2.0
"""The Leadership brief on the steady 1 Nov fixture (M8 acceptance): made by the nightly run
with no model, P1 (South/West) first, its title clauses from the computed drill-down, tiles
against their bands, Ask today for the owner of customers, the goal line from M7, counts that
agree with Sweep, and replay identical (the issues are part of the run's outputs).
Needs `make dev && make seed`."""

from __future__ import annotations

import sqlalchemy as sa

from othor.briefs.models import Brief, BriefIssue, BriefVersion
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.engine.replay import replay
from othor.engine.store import RunFiles, read_json
from tests.integration.test_sweep_sample import night  # noqa: F401


def test_the_leadership_brief_of_1_nov(night):  # noqa: F811
    ctx, cid, fid, rid = night[:4]
    with ctx.sf() as s, tenant_scope(cid):
        (b,) = s.scalars(sa.select(Brief)).all()
        assert b.name == "Leadership" and b.is_default and b.current_version == 1
        (v,) = s.scalars(sa.select(BriefVersion)).all()
        assert v.body["question"] == (
            "Are we on track for ₹60 Cr this year, and where do I need to step in?"
        )
        assert v.body["headline"][0] == "net_revenue"
        assert s.scalars(
            sa.select(Event).where(Event.action == "brief.created", Event.object_id == str(b.id))
        ).one()
        (issue,) = s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id == rid)).all()
        body = issue.body
        # the fixture run makes no brief (no Sweep), only the nightly one
        assert not s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id == fid)).all()
    items = body["items"]
    # the 1 Nov order, golden (decided 6 Nov 2026: the docs/04 formula, the tie-break within
    # 10% puts P1's earlier consequence date ahead of P7; the board's P1–P4 is a sample-data
    # difference)
    assert [(i["detector"], i["metric"]) for i in items] == [
        ("net_hiding_gross", "net_revenue"),
        ("rising_variance", "dispatch_time"),
        ("missing_records", "invoiced_value"),
        ("broken_relationship", "freight"),
    ]
    assert all(i["state"] != "needs_confirmation" for i in items)
    assert body["title"] == (
        "South fell ₹1.4 Cr while West’s growth hid it; three accounts carry 72% of the drop "
        "and none has invoiced since 9 Oct; one is Skarfab’s known pause to 1 Dec."
    )
    assert body["checks"] == {"title": [], "ask": []}
    assert body["ask"]["to"] == "Priya Menon"
    # only the accounts with no explanation: Skarfab is under its known pause
    assert body["ask"]["text"] == (
        "Have Tessora EU and Ravira Exports moved orders elsewhere, or are they on hold?"
    )
    (rev,) = [t for t in body["tiles"] if t["metric"] == "net_revenue"]
    assert rev["value"] == "₹4.62 Cr" and rev["comparison"]["kind"] == "band"
    (fy,) = [g for g in body["goals"] if g["key"] == "fy27_net_revenue"]
    assert fy["text"].startswith("Net revenue FY27 · ₹60 Cr target · tracking ₹57.2 Cr (80% band ₹")
    assert fy["tone"] in ("off", "risk")
    # counts agree: the items shown plus "N more in Sweep" are Sweep's findings
    sweep = read_json(RunFiles(ctx.settings.runs_dir(cid), rid).outputs_json)["sweep"]
    assert body["more_in_sweep"] + len(items) == sweep["counts"]["findings"]
    assert not body["quiet"] and body["prose"] == {"source": "template"}
    with tenant_scope(cid):
        assert replay(ctx, rid).ok
