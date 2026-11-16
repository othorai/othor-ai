# SPDX-License-Identifier: Apache-2.0
"""The scan at board scale (docs/05: "Layout must work for 48 or 4,000 tables").

Opt-in: `make test-large` after `make seed SCALE=large`. Scans the 4,000-table `scan_stress`
schema and the board-size `perf` tables through the job runner, and pages the list.
"""

from __future__ import annotations

import datetime as dt
import os
import time

import pytest
import sqlalchemy as sa

from othor.connectors import service
from othor.connectors.models import RunQuery, Source, SourceColumn, SourceTable
from othor.connectors.sql.query_log import identifiers
from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.tenancy import tenant_scope
from othor.jobs.runner import WorkerContext, run_pending
from othor.web import sources_view as sv

pytestmark = pytest.mark.skipif(
    os.environ.get("OTHOR_TEST_LARGE") != "1", reason="opt-in: make test-large"
)
NOW = dt.datetime(2026, 11, 4, 10, 12, tzinfo=dt.UTC)


def test_scan_4000_tables_and_perf(session_factory, two_companies, tmp_path, db_url):
    if not db_url.startswith("sqlite"):
        pytest.skip("one metadata DB is enough for the timing")
    cid, _ = two_companies
    ctx = WorkerContext(
        Settings(env="test", data_dir=tmp_path, db_url="sqlite://"),
        session_factory,
        InstanceKey.load_or_create(tmp_path / "k", allow_create=True),
        clock=lambda: NOW,
    )
    with ctx.sf() as s, tenant_scope(cid):
        src = service.create_sql_source(
            s,
            ctx.key,
            cid,
            kind="postgres",
            password="othor_ro_dev",
            actor="t",
            in_setup=True,
            config={
                "host": "localhost",
                "port": 5443,
                "database": "sales_dw",
                "user": "othor_ro",
                "ssl": "disable",
                "schemas": ["scan_stress", "perf"],
            },
        )
        service.request_test(s, src)
        s.commit()
        sid = src.id
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        src = s.scalars(sa.select(Source).where(Source.id == sid)).one()
        assert src.last_test["ok"], src.last_test
        assert src.last_test["tables"] == 4004
        service.connect(s, src, actor="t", now=NOW)
        s.commit()
    t0 = time.perf_counter()
    run_pending(ctx)
    took = time.perf_counter() - t0
    print(f"\nscan of 4,004 tables: {took:.1f} s")
    with ctx.sf() as s, tenant_scope(cid):
        tables = s.scalars(sa.select(SourceTable)).all()
        assert len(tables) == 4004 and all(t.state == "done" for t in tables)
        perf = {t.name: t for t in tables if t.schema == "perf"}
        assert perf["fact_invoice_line"].row_count_estimated
        assert perf["fact_invoice_line"].row_count > 6_000_000
        uses = {t.use for t in tables}
        assert {"metric_source", "dimension", "ignored", "excluded"} <= uses
        # personal-looking columns in the stress tables are never selected
        by_table: dict[str, set[str]] = {}
        names = {t.id: t.name.lower() for t in tables}
        for c in s.scalars(sa.select(SourceColumn).where(SourceColumn.is_personal.is_(True))):
            by_table.setdefault(names[c.table_id], set()).add(c.name.lower())
        assert by_table, "the stress schema has personal-looking tables"
        for q in s.scalars(sa.select(RunQuery)):
            ids = identifiers(q.sql)
            for table, cols in by_table.items():
                assert not (table in ids and cols & ids), q.sql
        # the list pages: 50 a page, every tab
        src = s.scalars(sa.select(Source).where(Source.id == sid)).one()
        tl = sv.table_list(s, src, tab="ignored", page=3)
        assert tl.pages > 3 and len(tl.rows) == sv.PAGE_SIZE
        hits = sv.table_list(s, src, tab="all", q="bak")
        assert hits.total > 0
        view = sv.scan_view(s, src)
        assert view.line.startswith("4,004 of 4,004 tables · 2 of 2 schemas done")
    assert took < 600, f"scan took {took:.0f} s"
