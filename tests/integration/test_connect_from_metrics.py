# SPDX-License-Identifier: Apache-2.0
"""Connect a source from Metrics (M4b: 2nca, 2ncb). The Order tracker, connected for Order book
value, redrafts that row from the new tabs with today's value computed, and adds the drafts the
source unlocks. Without a model, by rules."""

from __future__ import annotations

import duckdb
import pytest
import sqlalchemy as sa

from othor.connectors import service
from othor.connectors.models import SheetTab, Source
from othor.core.tenancy import tenant_scope
from othor.jobs.runner import run_pending
from othor.setup import connect
from othor.setup.models import SetupDraft
from tests.fixtures.sample_company.generate import generate
from tests.fixtures.sample_company.load import write_sheets
from tests.integration import setup_world as w


@pytest.fixture(scope="module")
def sample():
    return generate()


@pytest.fixture
def need_sources(db_url, sample_sources_seeded):
    if not db_url.startswith("sqlite"):
        pytest.skip("one metadata DB is enough")
    from othor.connectors.sql.scan import check_connection
    from tests.conftest import require_services
    from tests.integration.test_sql_connectors import my

    if check_connection(my("othor_ro")).message.startswith("Can't reach"):
        require_services("sample sources")


def _connect_tracker(ctx, cid, sample, key):
    with ctx.sf() as s, tenant_scope(cid):
        src = service.create_sheets_source(
            s,
            ctx.key,
            cid,
            files=[{"id": "sample-order-tracker-fy27"}],
            secret={"fixture": True},
            account="uday@indigothread.example",
            actor=w.OWNER,
            in_setup=True,
        )
        if key:
            connect.mark_for(src, key)
        else:
            src.in_setup = False
        service.start_scan(s, src, actor=w.OWNER)
        s.commit()
        sid = src.id
    run_pending(ctx)
    from othor.connectors.sheets.detect import detect
    from othor.connectors.sheets.extract import header_fingerprint

    with ctx.sf() as s, tenant_scope(cid):
        src = s.scalars(sa.select(Source).where(Source.id == sid)).one()
        oo = s.scalars(sa.select(SheetTab).where(SheetTab.tab_name == "Open orders")).one()
        raw = next(
            x
            for x in sample.sheets["sample-order-tracker-fy27"]["sheets"]
            if x["properties"]["title"] == "Open orders"
        )
        fp = header_fingerprint(detect(raw, today=w.NOW.date()))
        service.confirm_tab(s, oo, actor=w.OWNER, now=w.NOW, fingerprint=fp)
        service.finish_sheets(s, src, actor=w.OWNER, now=w.NOW)
        connect.connected(s, src, actor=w.OWNER)  # read now, then redraft for the metric
        s.commit()
    run_pending(ctx)
    return sid


def test_order_tracker_redrafts_order_book_value(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    from tests.fakes_model import DRAFT, FakeClaude

    cid, _ = two_companies
    out = {**DRAFT, "missing": [{"name": "Order book value", "why": "Needs the Order tracker"}]}
    claude = w.context(
        session_factory, tmp_path, "anthropic", "sk-ant-test", gateway_provider=FakeClaude(out)
    )
    write_sheets(sample, tmp_path / "sample" / "sheets")
    w.build(claude, cid, sample, tmp_path)
    w.draft(claude, cid)
    ctx = w.context(session_factory, tmp_path)  # connecting works without the model
    with ctx.sf() as s, tenant_scope(cid):
        row = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "order_book_value")).one()
        assert row.state == "needs_source"
        before = {
            r.key for r in s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "metric"))
        }

    sid = _connect_tracker(ctx, cid, sample, "order_book_value")

    with ctx.sf() as s, tenant_scope(cid):
        row = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "order_book_value")).one()
        assert row.state == "needs_confirmation" and row.body["status"] == "active"
        assert (
            row.body["table"]["table"] == "Open orders" and row.body["name"] == "Order book value"
        )
        assert row.value and row.value["state"] == "ok"
        oo = s.scalars(sa.select(SheetTab).where(SheetTab.tab_name == "Open orders")).one()
        from othor.jobs.read_job import duckdb_table

        con = duckdb.connect(str(ctx.settings.analytics_path(cid)), read_only=True)
        (want,) = con.execute(f'SELECT sum(value) FROM "{duckdb_table(oo)}"').fetchone()
        con.close()
        assert float(row.value["value"]) == pytest.approx(float(want), abs=0.01)
        added = [
            r
            for r in s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "metric"))
            if r.key not in before
        ]
        assert added and all((r.origin or {}).get("source_id") == str(sid) for r in added)
        assert all(r.body["table"]["source"] != "erp_db" for r in added)
        view = connect.review(s, sid)
        assert view.target is not None and view.target.key == "order_book_value"
        assert {r.key for r in view.added} == {r.key for r in added}


def test_after_setup_new_source_drafts_and_confirm_writes_a_version(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    """6h: after Confirm, the Order tracker redrafts the metric waiting on it (not Win rate, a
    rate), adds new drafts, and "Confirm N metrics" writes one new compiled version."""
    from othor.core.models import Company
    from othor.ontology import service as ontology
    from othor.ontology.models import Spec
    from othor.setup import confirm
    from tests.fakes_model import DRAFT, FakeClaude

    cid, _ = two_companies
    out = {
        **DRAFT,
        "metrics": [m for m in DRAFT["metrics"] if m["key"] != "bad_column"],
        "missing": [
            {"name": "Order book value", "why": "Needs the Order tracker"},
            {"name": "Win rate", "why": "Needs the Order tracker deals"},
        ],
    }
    claude = w.context(
        session_factory, tmp_path, "anthropic", "sk-ant-test", gateway_provider=FakeClaude(out)
    )
    write_sheets(sample, tmp_path / "sample" / "sheets")
    w.build(claude, cid, sample, tmp_path)
    w.draft(claude, cid)
    ctx = w.context(session_factory, tmp_path)
    with ctx.sf() as s, tenant_scope(cid):
        nr = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "net_revenue")).one()
        if nr.confidence == "conflict":
            confirm.choose(s, nr, "source", actor=w.OWNER)
        s.commit()
    w.draft(ctx, cid, "values")
    with ctx.sf() as s, tenant_scope(cid):
        confirm.confirm_setup(s, s.get(Company, cid), actor=w.OWNER, today=w.NOW.date())
        s.commit()

    sid = _connect_tracker(ctx, cid, sample, None)

    with ctx.sf() as s, tenant_scope(cid):
        view = connect.review(s, sid)
        keys = [r.key for r in view.rows]
        assert keys[0] == "order_book_value" and view.waiting == 1
        assert "win_rate" not in keys
        obv = view.rows[0]
        assert obv.state == "needs_confirmation" and obv.value["state"] == "ok"
        new = [r.key for r in view.rows[1:]]
        assert new
        before = ontology.current_number(s)
        v = confirm.confirm_more(
            s,
            s.get(Company, cid),
            ["order_book_value", new[0]],
            actor=w.OWNER,
            today=w.NOW.date(),
            reason="Connected the Order tracker",
        )
        s.commit()
        assert v is not None and v.number == before + 1  # one version for all of them
        live = {r.key: r for r in ontology.active(s, {"metric"})}
        assert live["order_book_value"].body["status"] == "active"
        assert live["win_rate"].body["status"] == "needs_source"
        assert s.scalars(sa.select(Spec).where(Spec.ontology_version == v.number)).one()
