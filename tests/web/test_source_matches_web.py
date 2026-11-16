# SPDX-License-Identifier: Apache-2.0
"""6h cross-source items (answered 5 Nov 2026), on the Order tracker variant: the review step says
matching runs tonight; after the nightly read the source's pane shows the match ("…% of Order
tracker FY27 customers match erp_db customers by name"), the unmatched by value, and a join
proposal; Accept writes the join rule as a new Ontology version. Needs `make dev && make seed`.
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from othor.connectors.models import Source
from othor.core.models import Company, Event
from othor.core.tenancy import system_scope, tenant_scope
from othor.engine import orchestrator
from othor.engine.context import EngineContext
from othor.engine.models import Proposal
from othor.ontology.models import OntologyRecord
from tests.fixtures.sample_company.load import write_sheets
from tests.integration.run_world import confirm_sample
from tests.web.conftest import Env, signup
from tests.web.test_chat_web import need_sources, sample  # noqa: F401


def test_matching_shows_on_the_source_and_a_join_can_be_accepted(env: Env, sample, need_sources):  # noqa: F811
    env.clock.t = dt.datetime(2026, 11, 1, 3, 0, tzinfo=dt.UTC)
    signup(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, system_scope("test"):
        cid = s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one()
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    write_sheets(sample, ctx.settings.sample_dir / "sheets")
    confirm_sample(ctx, cid, sample, order_tracker=True)
    h = env.host("indigothread")
    with svc.sf() as s, tenant_scope(cid):
        tracker = s.scalars(sa.select(Source).where(Source.kind == "gsheets")).one()
        tid = tracker.id
    review = env.client.get(f"{h}/sources?panel=review&s={tid}").text
    assert (
        "Matching with your other sources runs tonight. "
        "Results tomorrow on this source and on Home." in review
    )
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s,
            c,
            kind="nightly",
            run_date=dt.date(2026, 11, 1),
            now=ctx.clock(),
            image_version="0.1.0",
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    pane = env.client.get(f"{h}/sources?panel=manage&s={tid}").text
    assert "Matched with your other sources" in pane
    assert "customers match erp_db customers by name (" in pane
    with svc.sf() as s, tenant_scope(cid):
        (p,) = s.scalars(sa.select(Proposal).where(Proposal.kind == "join")).all()  # one per source
        tabs = p.evidence["tabs"]
        assert p.status == "open" and tabs and all(x["status"] == "open" for x in tabs)
        assert all(x["matched"] >= 20 and x["grade"] == "name" for x in tabs)
        assert p.text.startswith("Join the Order tracker FY27 to erp_db customers · ")
        pid, first = p.id, tabs[0]["key"]
    assert p.text in pane and "matched by name" in pane and "% match (" in pane
    tok = env.csrf(f"{h}/sources")
    from urllib.parse import quote

    r = env.client.post(
        f"{h}/sources/{tid}/proposals/{pid}/accept?tab={quote(first)}",
        data={"csrf": tok},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/sources?panel=manage&s={tid}"  # Manage stays open
    with svc.sf() as s, tenant_scope(cid):
        p = s.scalars(sa.select(Proposal).where(Proposal.id == pid)).one()
        done = {x["key"]: x["status"] for x in p.evidence["tabs"]}
        assert done[first] == "accepted" and (len(done) == 1 or p.status == "open")
        active = s.scalars(sa.select(OntologyRecord).where(OntologyRecord.status == "active")).all()
        joins = [x for x in active if x.type == "rule" and x.body.get("via") == "alias"]
        aliases = [
            x for x in active if x.type == "alias" and x.body.get("source") == "Order tracker FY27"
        ]
        assert len(joins) == 1 and len(aliases) >= 20  # never a join on raw names
        assert s.scalars(sa.select(Event).where(Event.action == "proposal.accepted")).one()
    assert "Accepted" in env.client.get(f"{h}/sources?panel=manage&s={tid}").text
    from othor.ontology.compiler import compile_version

    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        spec = compile_version(s, c).compiled
        (rule,) = [
            r for r in spec["rules"] if r["kind"] == "join" and r["text"].endswith("by name")
        ]
        assert rule["text"].startswith("Order tracker FY27 · ")
