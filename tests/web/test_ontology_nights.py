# SPDX-License-Identifier: Apache-2.0
"""M10b acceptance (decided 6 Nov 2026): Restore keeps history going.

On the steady night of 1 Nov: change a snapshot metric's meaning (a new series version on the
night of 2 Nov: its points are archived), then Restore the version before. On 3 Nov every metric's
definition hash is 1 Nov's again, the archived points are the series again (nothing restarts),
the restored version has the same content hash as the one restored, and the run replays.
Needs `make dev && make seed`.
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from othor.core.models import Company, Event
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.context import EngineContext
from othor.engine.models import Run
from othor.engine.replay import replay
from othor.engine.store import RunFiles, read_json, read_points
from othor.ontology import service
from othor.ontology.compiler import compile_version
from othor.ontology.models import OntologyVersion, Spec
from tests.fixtures.sample_company.history import STEADY_RUN
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

KEY = "receivables_open"


def _run(env, svc, cid, day: dt.date):
    env.clock.t = dt.datetime.combine(day, dt.time(3, 0), tzinfo=dt.UTC)
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s, c, kind="nightly", run_date=day, now=ctx.clock(), image_version="0.1.0"
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    return ctx, rid


def _defs(svc, cid, rid):
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
        return {m: e.get("definition_hash") for m, e in spec.compiled["metrics"].items()}


def _days(ctx, cid, rid):
    files = RunFiles(ctx.settings.runs_dir(cid), rid)
    pts = read_points(files.source_points("erp_db"))
    return sorted({p.period for p in pts if p.metric == KEY and p.breakdown == ""})


def test_restore_keeps_the_series_going(night):  # noqa: F811
    env, _h, cid, rid1, svc = night
    with svc.sf() as s, tenant_scope(cid):
        n = service.current_number(s)
        rec = next(r for r in service.active(s, ["metric"]) if r.key == KEY)
        # a change of meaning: a filter (0.28.3: a breakdown dropped restarts only that
        # breakdown's series, so it no longer serves here)
        floor = {"op": ">", "args": [rec.body["measure"]["expr"], {"lit": -(10**12)}]}
        body = {**rec.body, "filters": [*rec.body.get("filters", []), floor]}
        service.apply(s, [service.put("metric", KEY, body)], actor="o", reason="a floor")
        compile_version(s, s.scalars(sa.select(Company)).one(), actor="o")
        s.commit()
    ctx, rid2 = _run(env, svc, cid, STEADY_RUN + dt.timedelta(days=1))
    assert _defs(svc, cid, rid2)[KEY] != _defs(svc, cid, rid1)[KEY]
    before = _days(ctx, cid, rid1)
    assert len(_days(ctx, cid, rid2)) < len(before)  # versioned: the series started again

    with svc.sf() as s, tenant_scope(cid):
        m = service.restore(s, n, actor="o")
        assert m is not None and m.number > n
        restored = s.scalars(sa.select(OntologyVersion).where(OntologyVersion.number == n)).one()
        assert m.content_hash == restored.content_hash
        compile_version(s, s.scalars(sa.select(Company)).one(), actor="o")
        s.commit()
    ctx, rid3 = _run(env, svc, cid, STEADY_RUN + dt.timedelta(days=2))
    assert _defs(svc, cid, rid3) == _defs(svc, cid, rid1)
    days = _days(ctx, cid, rid3)
    assert days[0] == before[0] and set(before) <= set(days)  # history continues
    meta = read_json(RunFiles(ctx.settings.runs_dir(cid), rid3).source_meta("erp_db"))
    assert any(v.get("reinstated") for v in meta["versions"] if v["metric"] == KEY)
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalars(sa.select(Event).where(Event.action == "series.reinstated")).first()
    with tenant_scope(cid):
        assert replay(ctx, rid3).ok
