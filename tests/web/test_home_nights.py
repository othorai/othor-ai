# SPDX-License-Identifier: Apache-2.0
"""M10a acceptance (docs/10): accepting a suppression proposal on Home removes that finding from
the next run (it is covered by the new known pattern, listed under Suppressed), and the run of
that next night replays identically. Needs `make dev && make seed`.

The proposal row is the one M6b's rule writes (a finding on the same calendar window in two or
more years: unit-tested in tests/unit/engine/test_proposals.py); here it is made for a finding of
the 1 Nov run, so the next night shows the effect of Accept end to end.
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.context import EngineContext
from othor.engine.findings import pattern_of
from othor.engine.models import Finding, Proposal
from othor.engine.replay import replay
from othor.ontology import service
from tests.fixtures.sample_company.history import STEADY_RUN
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_home_web import _post
from tests.web.test_sweep_web import night  # noqa: F401

WINDOW_DETECTORS = {"changepoint", "net_hiding_gross", "rising_variance", "seasonal_break"}


def _next_night(env, svc, cid):
    env.clock.t = dt.datetime(2026, 11, 2, 3, 0, tzinfo=dt.UTC)
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s,
            c,
            kind="nightly",
            run_date=STEADY_RUN + dt.timedelta(days=1),
            now=ctx.clock(),
            image_version="0.1.0",
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    return ctx, rid


def test_an_accepted_suppression_covers_the_finding_from_the_next_run(night):  # noqa: F811
    env, h, cid, rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        found = [
            r.body
            for r in s.scalars(sa.select(Finding).where(Finding.run_id == rid))
            if r.status == "open" and r.detector in WINDOW_DETECTORS
        ]
        f = sorted(found, key=lambda x: x["fingerprint"])[0]
        p = Proposal(
            kind="suppression",
            text=f"{f['metric']}: the same finding around these dates. Mark it as a known pattern?",
            evidence={
                "years": [2025, 2026],
                "window": f["window"],
                "fingerprint": f["fingerprint"],
                "metric": f["metric"],
                "name": "Recurring dates",
                "confidence": "medium",
            },
            origin={"rule": "recurs_on_calendar_window", "finding": f["fingerprint"]},
            fingerprint=f"suppression:{pattern_of(f)}",
            status="open",
            run_id=rid,
        )
        s.add(p)
        s.commit()
        pid = str(p.id)
    assert "Suppression" in env.client.get(h + "/home").text
    assert _post(env, h, f"/home/proposals/{pid}/accept").status_code == 303
    with svc.sf() as s, tenant_scope(cid):
        (rec,) = [
            r for r in service.active(s, ["known_pattern"]) if r.source_ref == {"proposal": pid}
        ]
        assert rec.body["confirmed"] is True and rec.body["metrics"] == [f["metric"]]
    ctx, rid2 = _next_night(env, svc, cid)
    with svc.sf() as s, tenant_scope(cid):
        tonight = {
            r.fingerprint: r.status
            for r in s.scalars(sa.select(Finding).where(Finding.run_id == rid2))
        }
    assert tonight.get(f["fingerprint"]) != "open"
    page = env.client.get(h + "/sweep?tab=suppressed").text
    assert "Recurring dates" in page
    # and it is listed in the Ontology's Known patterns (M10b)
    ont = env.client.get(h + "/ontology").text
    known = ont[ont.index("Known patterns") :]
    assert "Recurring dates" in known and "Proposal on Home" in known
    with tenant_scope(cid):
        assert replay(ctx, rid2).ok
