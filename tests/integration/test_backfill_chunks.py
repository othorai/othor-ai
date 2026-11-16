# SPDX-License-Identifier: Apache-2.0
"""M15.1: the back-fill reads its span in chunks of 13 Sundays (on AWS, one statement over 104
weeks of a 7 M-row table passed MySQL's 120 s limit and the night ended partial). The chunks give
the same points as one statement; a back-fill that failed runs again the next night."""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator, periods
from othor.engine import sources as engine_sources
from othor.engine.models import RunStep
from othor.engine.store import RunFiles, read_json, read_points
from tests.fixtures.sample_company.history import FIRST_DAYS_RUN
from tests.integration.run_world import confirm_sample, engine_ctx, require_sources


def _night(ctx, cid, run_date):
    with ctx.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s, c, kind="nightly", run_date=run_date, now=ctx.clock(), image_version="0.1.0"
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        status = orchestrator.execute(ctx, rid)
    return rid, status


def _backfill(ctx, cid, rid):
    return read_points(RunFiles(ctx.settings.runs_dir(cid), rid).backfill_points("erp_db"))


@pytest.fixture
def world(session_factory, two_companies, sample, tmp_path, sqlite_only, sample_sources_seeded):
    require_sources()
    ctx = engine_ctx(session_factory, tmp_path)
    a, b = two_companies
    confirm_sample(ctx, a, sample)
    confirm_sample(ctx, b, sample)
    return ctx, a, b


def test_chunks_give_the_same_points_as_one_statement(world, monkeypatch):
    ctx, a, b = world
    rid_b, status_b = _night(ctx, b, FIRST_DAYS_RUN)  # in chunks (the default)
    monkeypatch.setattr(periods, "backfill_chunks", lambda first, last: [(first, last)])
    rid_a, status_a = _night(ctx, a, FIRST_DAYS_RUN)  # one statement per query, as before
    assert status_a == status_b == "done"
    one, chunked = _backfill(ctx, a, rid_a), _backfill(ctx, b, rid_b)
    assert one and chunked == one


def test_a_failed_backfill_runs_again_the_next_night(world, monkeypatch):
    ctx, a, _b = world
    real = engine_sources.Executor.run

    def failing(self, sql, params=None):
        if "RECURSIVE" in sql.upper():  # only the back-fill builds a recursive calendar
            raise sa.exc.OperationalError(sql, {}, Exception(3024, "too slow"))
        return real(self, sql, params)

    monkeypatch.setattr(engine_sources.Executor, "run", failing)
    rid1, status1 = _night(ctx, a, FIRST_DAYS_RUN)
    assert status1 == "partial"
    with ctx.sf() as s, tenant_scope(a):
        step = s.scalars(
            sa.select(RunStep).where(RunStep.run_id == rid1, RunStep.name == "backfill:erp_db")
        ).one()
    assert step.status == "stale" and step.detail.get("code") == "3024"
    meta = read_json(RunFiles(ctx.settings.runs_dir(a), rid1).source_meta("erp_db"))
    assert meta["backfilled"] == {}  # nothing finished
    monkeypatch.setattr(engine_sources.Executor, "run", real)
    rid2, status2 = _night(ctx, a, FIRST_DAYS_RUN + dt.timedelta(days=1))
    assert status2 == "done"
    assert _backfill(ctx, a, rid2)  # tried again, and this time written
    meta = read_json(RunFiles(ctx.settings.runs_dir(a), rid2).source_meta("erp_db"))
    assert meta["backfilled"]  # recorded, so the night after won't redo it


def test_one_connection_per_source_and_step(world, monkeypatch):
    """M15.1: a source's statements in one step share one connection (a night opened ~380 TLS
    connections to the customer's databases); a statement that fails closes it, and the next
    statement opens a fresh one. Each statement is still its own read-only transaction."""
    ctx, a, _b = world
    opened: list[str] = []
    real = engine_sources.make_engine

    def counting(*args, **kw):
        eng = real(*args, **kw)
        sa.event.listen(eng, "connect", lambda *_: opened.append("c"))
        return eng

    monkeypatch.setattr(engine_sources, "make_engine", counting)
    rid, status = _night(ctx, a, FIRST_DAYS_RUN)
    assert status == "done"
    from othor.engine.models import Run

    with ctx.sf() as s, tenant_scope(a):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        info, _spec = orchestrator._info(ctx, s, run)
    queries = (run.summary or {}).get("queries") or 0
    assert queries > 20 and len(opened) < queries / 4  # one per source and step, not per query
    with tenant_scope(a), engine_sources.Executor(ctx, info, "erp_db") as ex:
        with pytest.raises(sa.exc.DBAPIError):
            ex.run("SELECT no_such_column FROM invoices")
        assert ex.run("SELECT 1") == [(1,)]
