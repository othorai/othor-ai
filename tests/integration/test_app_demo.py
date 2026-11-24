# SPDX-License-Identifier: Apache-2.0
"""`make demo`'s installer: Indigo Thread set up as an owner would leave onboarding, once."""

from __future__ import annotations

import sqlalchemy as sa

from othor.connectors import credentials
from othor.connectors.models import Source
from othor.core.models import Company, Membership, SetupState
from othor.core.tenancy import system_scope, tenant_scope
from othor.ontology import service
from tests.fixtures.sample_company import app_demo
from tests.integration.run_world import engine_ctx


def _ctx(tmp_path):
    from othor.core.db import make_engine, make_session_factory
    from othor.migrations.runner import upgrade

    url = f"sqlite:///{tmp_path / 'meta.db'}"
    upgrade(url)
    return engine_ctx(make_session_factory(make_engine(url)), tmp_path)


def test_installs_the_sample_company_once(tmp_path):
    ctx = _ctx(tmp_path)
    first = app_demo.install(ctx)
    assert first.created
    again = app_demo.install(ctx)
    assert not again.created and again.company_id == first.company_id

    with ctx.sf() as s, system_scope("test"):
        assert s.scalars(sa.select(sa.func.count()).select_from(Company)).one() == 1
    with ctx.sf() as s, tenant_scope(first.company_id):
        assert service.confirmed(s)
        assert s.scalars(sa.select(SetupState)).one().current_step == "done"
        assert s.scalars(sa.select(Membership)).one().role == "owner"
        sources = {x.name: x for x in s.scalars(sa.select(Source))}
        assert set(sources) == {"erp_db", "sales_dw"}
        # the hosts the app and worker containers reach, through the read-only login
        assert sources["erp_db"].config["host"] == "erp-db"
        assert sources["sales_dw"].config["user"] == "othor_ro"
        for src in sources.values():
            got = credentials.load(s, ctx.key, first.company_id, src.id)
            assert got["password"] == app_demo.PASSWORD
