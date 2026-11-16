# SPDX-License-Identifier: Apache-2.0
"""M18.3 on the built 1 Nov night: a deleted company's purge leaves no row in any tenant table
(specs, events, runs, briefs, sources, sessions…), no folder, and only a placeholder."""

from __future__ import annotations

import sqlalchemy as sa

from othor import closing, purging
from othor.core.models import Company, PlatformEvent
from othor.core.tenancy import system_scope, tenant_scope
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def test_a_real_night_purges_to_nothing(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    now = svc.clock()
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        closing.close(s, c, actor="test", now=now)
        s.commit()
    folder = svc.settings.company_dir(cid)
    assert folder.exists()
    with svc.sf() as s, system_scope("test: before"):
        before = {
            t.name: s.scalar(sa.select(sa.func.count()).select_from(t).where(t.c.company_id == cid))
            for t in purging.tenant_tables()
        }
    assert before["spec"] and before["event"] and before["run"] and before["finding"]
    later = now + closing.KEEP_FOR
    assert purging.tick(svc.sf, svc.settings, env.relay, later) == 1
    assert not folder.exists()
    with svc.sf() as s, system_scope("test: after"):
        for t in purging.tenant_tables():
            n = s.scalar(sa.select(sa.func.count()).select_from(t).where(t.c.company_id == cid))
            assert n == 0, t.name
        pe = s.scalars(
            sa.select(PlatformEvent).where(PlatformEvent.action == "company.purged")
        ).one()
        for name, n in before.items():
            assert pe.after["tables"].get(name, 0) == n, name
    # the company's host still 404s; the slug is free
    assert env.client.get(h + "/login", follow_redirects=False).status_code == 404
