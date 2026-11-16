# SPDX-License-Identifier: Apache-2.0
"""A run that reads a Google Sheets source (the Order tracker, through the stand-in): its tabs are
read whole into DuckDB and the metric SQL runs there; Sheets snapshots start at the first run (no
back-fill). The docs/11 stale fixture: the Sheets token is revoked overnight, so the next run marks
the Order tracker's series stale with their last good day and goes on. Needs the sample sources."""

from __future__ import annotations

import datetime as dt
import json

import sqlalchemy as sa

from othor.connectors.models import Source
from othor.core.tenancy import tenant_scope
from othor.engine.models import Run, RunStep, SeriesMeta
from othor.engine.replay import replay
from tests.fixtures.sample_company.load import write_sheets
from tests.integration.run_world import confirm_sample, engine_ctx, require_sources
from tests.integration.test_run_sample import FIRST, _files, _run, _series

TRACKER = "Order tracker FY27"


def test_sheets_run_and_revoked_token(
    session_factory,
    two_companies,
    sample,
    tmp_path,
    sqlite_only,
    sample_sources_seeded,
):
    require_sources()
    cid, _ = two_companies
    ctx = engine_ctx(session_factory, tmp_path)
    folder = ctx.settings.sample_dir / "sheets"
    write_sheets(sample, folder)
    ids = confirm_sample(ctx, cid, sample, order_tracker=True)
    r1 = _run(ctx, cid)
    with ctx.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == r1)).one()
        steps = {x.name: x for x in s.scalars(sa.select(RunStep).where(RunStep.run_id == r1))}
        assert run.status == "done", {k: (v.status, v.detail) for k, v in steps.items()}
        assert steps[f"read:{TRACKER}"].detail["tabs"] >= 2
        assert f"backfill:{TRACKER}" not in steps  # Sheets tabs have no history
    book = _series(ctx, cid, r1, "order_book_value", "day")
    assert book[(None, FIRST - dt.timedelta(days=1))].value > 0
    assert not any(p.reconstructed for p in book.values())
    assert _series(ctx, cid, r1, "win_rate", "month")  # derived from the tracker's deals

    # Sat 31 Oct → Sun 1 Nov in docs/11: the token is revoked before the next night
    auth = folder / "auth.json"
    auth.write_text(
        json.dumps({**json.loads(auth.read_text()), "revoked_at": "2026-11-02T20:00:00"})
    )
    r2 = _run(ctx, cid, kind="nightly", run_date=FIRST + dt.timedelta(days=1))
    with ctx.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == r2)).one()
        assert run.status == "partial" and run.summary["stale_sources"] == [TRACKER]
        src = s.scalars(sa.select(Source).where(Source.id == ids[TRACKER])).one()
        assert src.status == "disconnected"
        meta = s.scalars(
            sa.select(SeriesMeta).where(
                SeriesMeta.metric == "order_book_value",
                SeriesMeta.grain == "day",
                SeriesMeta.breakdown == "",
            )
        ).one()
        assert meta.stale and meta.last_good_at == FIRST - dt.timedelta(days=1)
        rev = s.scalars(
            sa.select(SeriesMeta).where(
                SeriesMeta.metric == "net_revenue",
                SeriesMeta.grain == "month",
                SeriesMeta.breakdown == "",
            )
        ).one()
        assert not rev.stale  # the databases were read as usual
    doc = json.loads(_files(ctx, cid, r2).outputs_json.read_bytes())
    assert doc["coverage"]["sources"][TRACKER]["status"] == "stale"
    with tenant_scope(cid):
        assert replay(ctx, r1).ok and replay(ctx, r2).ok
