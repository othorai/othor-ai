# SPDX-License-Identifier: Apache-2.0
"""Connect → test → scan → sort, through the job runner (metadata DB + compose sample sources).

Acceptance (docs/10 M2), end to end: `employee` is Excluded and no `run_query` row of the scan
names a personal column; a writer role is refused; the Open orders tab is read into DuckDB with
header row 3, the total row and the merged cells handled.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import uuid
from pathlib import Path

import duckdb
import pytest
import sqlalchemy as sa

from othor.ai.gateway import ModelGateway, ProviderReply
from othor.ai.models import ModelCall
from othor.connectors import service
from othor.connectors.models import RunQuery, SheetTab, Source, SourceTable
from othor.connectors.sql.query_log import identifiers
from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.jobs.runner import WorkerContext, run_pending
from othor.ontology.models import OntologyRecord
from tests.fixtures.sample_company import extras
from tests.fixtures.sample_company.generate import generate
from tests.fixtures.sample_company.load import write_sheets
from tests.integration.test_sql_connectors import pg

NOW = dt.datetime(2026, 11, 4, 10, 12, tzinfo=dt.UTC)
OWNER = "00000000-0000-0000-0000-00000000000a"


@pytest.fixture
def ctx(session_factory, tmp_path) -> WorkerContext:
    settings = Settings(env="test", data_dir=tmp_path, db_url="sqlite://")
    key = InstanceKey.load_or_create(tmp_path / "instance.key", allow_create=True)
    return WorkerContext(settings, session_factory, key, clock=lambda: NOW)


@pytest.fixture(autouse=True)
def _services(sample_sources_seeded):
    from othor.connectors.sql.scan import check_connection
    from tests.conftest import require_services

    r = check_connection(pg("othor_ro"))
    if not r.ok and r.message.startswith("Can't reach"):
        require_services("sample source sales_dw (localhost:5443)")


def _pg_config(user: str) -> dict:
    return {
        "host": "localhost",
        "port": 5443,
        "database": "sales_dw",
        "user": user,
        "ssl": "disable",
        "schemas": ["public", "sales_dw"],
    }


def _add(ctx, cid, user: str) -> uuid.UUID:
    with ctx.sf() as s, tenant_scope(cid):
        src = service.create_sql_source(
            s,
            ctx.key,
            cid,
            kind="postgres",
            config=_pg_config(user),
            password=f"{user}_dev",
            actor=OWNER,
            in_setup=True,
        )
        service.request_test(s, src)
        s.commit()
        return src.id


def _src(ctx, cid, sid) -> Source:
    with ctx.sf() as s, tenant_scope(cid):
        return s.scalars(sa.select(Source).where(Source.id == sid)).one()


def test_writer_is_refused_and_cannot_connect(ctx, two_companies):
    cid, _ = two_companies
    sid = _add(ctx, cid, "othor_rw")
    run_pending(ctx)
    src = _src(ctx, cid, sid)
    assert src.last_test["ok"] is False
    assert src.last_test["message"].startswith("This user can change data.")
    with ctx.sf() as s, tenant_scope(cid):
        row = s.scalars(sa.select(Source).where(Source.id == sid)).one()
        with pytest.raises(service.ActionRefused):
            service.connect(s, row, actor=OWNER, now=NOW)
        assert s.scalars(sa.select(Event).where(Event.action == "source.refused")).first()


def test_scan_sorts_and_never_reads_personal_columns(ctx, two_companies):
    cid, other = two_companies
    sid = _add(ctx, cid, "othor_ro")
    run_pending(ctx)
    assert _src(ctx, cid, sid).last_test["ok"]
    with ctx.sf() as s, tenant_scope(cid):
        row = s.scalars(sa.select(Source).where(Source.id == sid)).one()
        sc = service.connect(s, row, actor=OWNER, now=NOW)
        s.commit()
        scan_id = sc.id
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        src = s.scalars(sa.select(Source).where(Source.id == sid)).one()
        assert src.status == "connected" and src.schema_fingerprint
        tables = {f"{t.schema}.{t.name}": t for t in s.scalars(sa.select(SourceTable))}
        assert len(tables) == 48
        assert all(t.state == "done" for t in tables.values())
        emp = tables["public.employee"]
        assert (emp.use, emp.use_set_by, emp.personal_count) == ("excluded", "scan", 6)
        assert tables["sales_dw.fact_invoice"].looks_like.startswith("Invoice ·")
        # every table's default use is an Ontology record, from the scan
        recs = s.scalars(sa.select(OntologyRecord).where(OntologyRecord.type == "table_use")).all()
        assert len(recs) == 48 and all(r.source_ref == {"scan": str(scan_id)} for r in recs)
        # guard 1 (docs/11): no query of this scan names a personal column of its table
        queries = s.scalars(sa.select(RunQuery).where(RunQuery.scan_id == scan_id)).all()
        assert len(queries) > 48
        personal = {(t, c.lower()) for src_, _s, t, c in extras.PERSONAL if src_ == "sales_dw"}
        for q in queries:
            ids = identifiers(q.sql)
            assert not any(t in ids and c in ids for t, c in personal), q.sql
        assert s.scalars(sa.select(Event).where(Event.action == "scan.finished")).first()
    # the other company sees none of it
    with ctx.sf() as s, tenant_scope(other):
        assert s.scalars(sa.select(SourceTable)).first() is None
        assert s.scalars(sa.select(RunQuery)).first() is None


def test_use_menu_rules_and_undo(ctx, two_companies):
    cid, _ = two_companies
    sid = _add(ctx, cid, "othor_ro")
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        service.connect(
            s, s.scalars(sa.select(Source).where(Source.id == sid)).one(), actor=OWNER, now=NOW
        )
        s.commit()
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        get = lambda n: s.scalars(sa.select(SourceTable).where(SourceTable.name == n)).one()  # noqa: E731
        emp, fact, bak = get("employee"), get("fact_order"), get("fact_invoice_bak_2023")
        with pytest.raises(service.ActionRefused):
            service.set_use(s, emp, "dimension", None, actor=OWNER, now=NOW)
        with pytest.raises(service.ActionRefused):
            service.set_use(s, fact, "ignored", None, actor=OWNER, now=NOW)  # needs a reason
        ev = service.set_use(s, fact, "ignored", "not_needed", actor=OWNER, now=NOW)
        assert service.menu_state(fact).kind == "user_ignored"
        assert service.menu_state(bak).kind == "scan_ignored"
        service.bring_back(s, bak, "metric_source", actor=OWNER, now=NOW)
        assert (bak.use, bak.scan_reason) == ("metric_source", "backup")  # scan reason kept
        service.use_without_personal(s, emp, "dimension", actor=OWNER, now=NOW)
        assert (emp.use, emp.without_personal) == ("dimension", True)
        service.keep_excluded(s, emp, actor=OWNER, now=NOW)
        assert emp.use == "excluded"
        before = s.scalars(sa.select(Event).where(Event.id == ev)).one().before
        service.undo_use(s, fact, before, actor=OWNER, now=NOW)
        assert (fact.use, fact.use_set_by) == ("metric_source", "scan")
        s.commit()
        active = s.scalars(
            sa.select(OntologyRecord).where(
                OntologyRecord.type == "table_use",
                OntologyRecord.status.in_(("active", "draft")),
            )
        ).all()
        assert len(active) == 48  # one active record per table, history retired
    # a rescan keeps the owner's choices
    with ctx.sf() as s, tenant_scope(cid):
        src = s.scalars(sa.select(Source).where(Source.id == sid)).one()
        bak = s.scalars(
            sa.select(SourceTable).where(SourceTable.name == "fact_invoice_bak_2023")
        ).one()
        assert bak.use == "metric_source"
        service.start_scan(s, src, actor=OWNER)
        s.commit()
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        bak = s.scalars(
            sa.select(SourceTable).where(SourceTable.name == "fact_invoice_bak_2023")
        ).one()
        assert (bak.use, bak.use_set_by) == ("metric_source", "user")


class FakeProvider:
    name = "fake"
    configured = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def call(self, system, messages, schema, max_tokens=0):
        user = messages[-1]["content"][-1]["text"]
        self.calls.append(user)
        tables = json.loads(user)["tables"]
        labels = []
        for t in tables:
            label = f"Label for {t['table'].split('.')[-1]}"
            if t["table"].endswith("fact_order"):
                label = "Orders · 999 invented"  # an invented number: guard keeps the rules label
            labels.append({"key": t["key"], "label": label})
        return ProviderReply(json.dumps({"labels": labels}), "fake-model", 100, 50)


def test_ai_labels_are_guarded(ctx, two_companies):
    cid, _ = two_companies
    fake = FakeProvider()
    ctx.gateway = ModelGateway(ctx.sf, fake)
    sid = _add(ctx, cid, "othor_ro")
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        service.connect(
            s, s.scalars(sa.select(Source).where(Source.id == sid)).one(), actor=OWNER, now=NOW
        )
        s.commit()
    run_pending(ctx)
    for sent in fake.calls:  # no personal column name ever reaches the model
        for _src, _s, _t, col in extras.PERSONAL:
            assert f'"{col}"' not in sent
        assert "employee" not in sent and "crm_contacts" not in sent  # excluded tables
    with ctx.sf() as s, tenant_scope(cid):
        t = {x.name: x for x in s.scalars(sa.select(SourceTable))}
        assert (t["fact_invoice"].looks_like, t["fact_invoice"].looks_like_by) == (
            "Label for fact_invoice",
            "ai",
        )
        assert t["fact_order"].looks_like_by == "rules"
        assert t["employee"].looks_like_by == "rules"
        calls = s.scalars(sa.select(ModelCall)).all()
        assert calls and all(c.guard_result == "passed" for c in calls)
    # same input again: answered from the cache, no new provider call
    n = len(fake.calls)
    with ctx.sf() as s, tenant_scope(cid):
        service.start_scan(
            s, s.scalars(sa.select(Source).where(Source.id == sid)).one(), actor=OWNER
        )
        s.execute(sa.update(SourceTable).values(looks_like_by="rules"))
        s.commit()
    run_pending(ctx)
    assert len(fake.calls) == n


def test_provider_down_keeps_rules_labels(ctx, two_companies):
    cid, _ = two_companies

    class Down:
        name = "down"
        configured = True

        def call(self, *a, **k):
            raise ConnectionError("provider down")

    ctx.gateway = ModelGateway(ctx.sf, Down())
    sid = _add(ctx, cid, "othor_ro")
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        service.connect(
            s, s.scalars(sa.select(Source).where(Source.id == sid)).one(), actor=OWNER, now=NOW
        )
        s.commit()
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        assert s.scalars(sa.select(Source).where(Source.id == sid)).one().status == "connected"
        assert {t.looks_like_by for t in s.scalars(sa.select(SourceTable))} == {"rules"}
        assert {c.guard_result for c in s.scalars(sa.select(ModelCall))} == {"error"}


# --- Google Sheets (stand-in) ------------------------------------------------------------


@pytest.fixture
def sheets_ctx(ctx, tmp_path) -> WorkerContext:
    write_sheets(generate(), tmp_path / "sample" / "sheets")
    return ctx


def _sheets(ctx, cid) -> uuid.UUID:
    with ctx.sf() as s, tenant_scope(cid):
        src = service.create_sheets_source(
            s,
            ctx.key,
            cid,
            files=[{"id": "sample-order-tracker-fy27"}],
            secret={"fixture": True},
            account="uday@indigothread.example",
            actor=OWNER,
            in_setup=True,
        )
        service.start_scan(s, src, actor=OWNER)
        s.commit()
        return src.id


def test_sheets_check_confirm_and_read_open_orders(sheets_ctx, two_companies):
    ctx = sheets_ctx
    cid, _ = two_companies
    sid = _sheets(ctx, cid)
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        tabs = {t.tab_name: t for t in s.scalars(sa.select(SheetTab))}
        assert set(tabs) == {
            "Open orders",
            "Deals",
            "Activities",
            "Products",
            "Contacts",
            "Summary",
        }
        oo = tabs["Open orders"]
        assert (oo.status, oo.header_row, oo.range, oo.selected) == ("unchecked", 3, "A4:H88", True)
        assert oo.shape["preview"]["rows"][0][0] == 1
        assert tabs["Deals"].status == "confirmed" and tabs["Deals"].confirmed_by == "scan"
        assert not tabs["Contacts"].selected and "preview" not in (tabs["Contacts"].shape or {})
        assert not tabs["Summary"].selected
        src = s.scalars(sa.select(Source).where(Source.id == sid)).one()
        assert src.config["files"][0]["title"] == "Order tracker FY27"
        assert src.config["files"][0]["editor"] == "Priya Menon"
        with pytest.raises(service.ActionRefused):
            service.finish_sheets(s, src, actor=OWNER, now=NOW)
        from othor.connectors.sheets.detect import detect
        from othor.connectors.sheets.extract import header_fingerprint

        fp = header_fingerprint(
            detect(
                next(
                    x
                    for x in generate().sheets["sample-order-tracker-fy27"]["sheets"]
                    if x["properties"]["title"] == "Open orders"
                ),
                today=NOW.date(),
            )
        )
        service.confirm_tab(s, oo, actor=OWNER, now=NOW, fingerprint=fp)
        assert "preview" not in oo.shape  # rows are not kept after the check
        service.finish_sheets(s, src, actor=OWNER, now=NOW)
        service.read_now(s, src, actor=OWNER)
        s.commit()
    run_pending(ctx)
    path = ctx.settings.analytics_path(cid)
    con = duckdb.connect(str(path), read_only=True)
    with ctx.sf() as s, tenant_scope(cid):
        oo = s.scalars(sa.select(SheetTab).where(SheetTab.tab_name == "Open orders")).one()
        from othor.jobs.read_job import duckdb_table

        n, metres, value = con.execute(
            f'SELECT count(*), sum(metres), sum(value) FROM "{duckdb_table(oo)}"'
        ).fetchone()
        src = s.scalars(sa.select(Source).where(Source.id == sid)).one()
        assert src.health["last_read"]["tabs"] == 4  # Open orders, Deals, Activities, Products
    con.close()
    assert n == 85
    assert metres == 1_284_000
    assert float(value) / 100_000 == pytest.approx(1512.4)


def test_sheets_revoked_and_access_removed(sheets_ctx, two_companies, tmp_path):
    ctx = sheets_ctx
    cid, _ = two_companies
    folder: Path = tmp_path / "sample" / "sheets"
    sid = _sheets(ctx, cid)
    (folder / "sample-order-tracker-fy27.json").rename(folder / "gone.json")
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        assert s.scalars(
            sa.select(Event).where(Event.action == "sheet_file.access_removed")
        ).first()
    shutil.move(folder / "gone.json", folder / "sample-order-tracker-fy27.json")
    auth = json.loads((folder / "auth.json").read_text())
    (folder / "auth.json").write_text(json.dumps({**auth, "revoked_at": "2026-11-01T02:00:00Z"}))
    with ctx.sf() as s, tenant_scope(cid):
        service.start_scan(
            s, s.scalars(sa.select(Source).where(Source.id == sid)).one(), actor=OWNER
        )
        s.commit()
    run_pending(ctx)
    src = _src(ctx, cid, sid)
    assert src.status == "disconnected"
    assert src.config["files"] == [{"id": "sample-order-tracker-fy27"}]  # same files kept
