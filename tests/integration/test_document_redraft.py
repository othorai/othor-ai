# SPDX-License-Identifier: Apache-2.0
"""Redraft with your document (M4b: 2ne, 2nf). The sample data dictionary, read by code without a
model, settles the GST conflict, adds the status X filter, the EXP vocabulary entry and the yards
conversion, and notes the rebates account; each change cites its page. A model's change whose
quote isn't in the document is dropped."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.jobs.queue import enqueue
from othor.jobs.runner import run_pending
from othor.ontology.compiler.catalog import load_catalog
from othor.setup import document, drafts, uploads
from othor.setup.models import SetupChange, SetupDraft
from tests.fixtures.sample_company.generate import generate
from tests.fixtures.sample_company.uploads import DICTIONARY, write_dictionary
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


def _world(session_factory, two_companies, tmp_path, sample):
    cid, _ = two_companies
    ctx = w.context(session_factory, tmp_path)
    w.build(ctx, cid, sample, tmp_path, skip=(DICTIONARY,))
    w.draft(ctx, cid)
    return ctx, cid


def _upload_dictionary(ctx, cid, tmp_path):
    (tmp_path / "doc").mkdir(exist_ok=True)
    path = write_dictionary(tmp_path / "doc" / DICTIONARY)
    with ctx.sf() as s, tenant_scope(cid):
        up, _ = uploads.add(
            s, ctx.settings, ctx.key, cid, filename=path.name, data=path.read_bytes(), actor=w.OWNER
        )
        enqueue(s, "draft", {"mode": "document", "upload_id": str(up.id), "actor": w.OWNER})
        s.commit()
    run_pending(ctx)
    return path


def test_dictionary_redrafts_by_rules(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    ctx, cid = _world(session_factory, two_companies, tmp_path, sample)
    with ctx.sf() as s, tenant_scope(cid):
        nr = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "net_revenue")).one()
        assert nr.confidence == "conflict"
    _upload_dictionary(ctx, cid, tmp_path)
    with ctx.sf() as s, tenant_scope(cid):
        changes = {(c.subject, c.result): c for c in s.scalars(sa.select(SetupChange))}
        results = {r for _, r in changes}
        assert {
            "conflict_resolved",
            "filter_added",
            "dimension_updated",
            "rule_added",
            "noted",
        } <= results
        gst = changes[("Net revenue", "conflict_resolved")]
        assert gst.locator == "p. 7" and gst.quote == "net_value already excludes GST"
        assert changes[("Region", "dimension_updated")].locator == "p. 8"
        conv = next(c for c in changes.values() if c.result == "rule_added")
        assert conv.locator == "p. 12"
        md = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == conv.draft_key)).one()
        assert md.body["table"]["table"] == "fact_dispatch"

        nr = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "net_revenue")).one()
        assert nr.confidence != "conflict" and nr.draft_no == 2
        assert nr.refs[0]["locator"] == "p. 7"
        assert (nr.origin or {}).get("document", {}).get("round") == 2
        assert nr.value and nr.value["state"] == "ok"
        kinds = {(r.kind, r.key) for r in s.scalars(sa.select(SetupDraft))}
        assert ("vocabulary", "region_exp") in kinds
        assert ("rule", "invoices_status_x") in kinds
        assert any(k == "rule" and "dispatch_mtrs" in key for k, key in kinds)
        meta = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "meta")).one()
        assert meta.body["round"] == 2 and meta.body["state"] == "done"
        md = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == conv.draft_key)).one()
        assert md.value and md.value["state"] == "ok"


def test_a_model_quote_not_in_the_document_is_dropped(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    ctx, cid = _world(session_factory, two_companies, tmp_path, sample)
    pages = [("p. 7", "status X means cancelled; cancelled invoices are left out of every report.")]
    with ctx.sf() as s, tenant_scope(cid):
        rows = drafts.stored(s)
        cat = load_catalog(s)
        patch = {
            "kind": "add_filter",
            "args": {"column": "status", "op": "<>", "value": "X"},
        }
        out = {
            "changes": [
                {"metric": "net_revenue", "quote": "status X means cancelled", "patch": patch},
                {"metric": "net_revenue", "quote": "status Z means void", "patch": patch},
                {
                    "metric": "net_revenue",
                    "quote": "status X means cancelled",
                    "patch": {
                        "kind": "add_filter",
                        "args": {"column": "gstin", "op": "=", "value": "x"},
                    },
                },
            ]
        }
        got = document.model_changes(out, pages, rows, cat, {})
    assert [(c.key, c.locator) for c in got] == [("net_revenue", "p. 7")]


def test_a_model_mapping_must_be_about_the_metric(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    """A quote naming a column maps only to a metric the quote is about; a formula is never
    replaced by one column."""
    ctx, cid = _world(session_factory, two_companies, tmp_path, sample)
    pages = [("p. 12", "Metres shipped are in sales_dw.fact_dispatch.dispatch_mtrs.")]
    patch = {
        "kind": "use_column",
        "args": {
            "table": {"source": "sales_dw", "schema": "sales_dw", "table": "fact_dispatch"},
            "column": "dispatch_mtrs",
            "date": "dispatch_date",
        },
    }
    quote = "Metres shipped are in sales_dw.fact_dispatch.dispatch_mtrs"
    with ctx.sf() as s, tenant_scope(cid):
        rows = drafts.stored(s)
        cat = load_catalog(s)
        out = {"changes": [{"metric": "net_revenue", "quote": quote, "patch": patch}]}
        assert document.model_changes(out, pages, rows, cat, {}) == []


def test_rules_never_map_metres_onto_a_ratio(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    ctx, cid = _world(session_factory, two_companies, tmp_path, sample)
    pages = [("p. 12", "Metres shipped are in sales_dw.fact_dispatch.dispatch_mtrs.")]
    from othor.setup.models import SetupDraft as SD

    with ctx.sf() as s, tenant_scope(cid):
        ratio = SD(
            kind="metric",
            key="returns_rate",
            body={
                "name": "Returns rate",
                "status": "active",
                "unit": "percent",
                "formula": {
                    "op": "/",
                    "args": [{"metric": "net_revenue"}, {"metric": "net_revenue"}],
                },
            },
            note="Is the returns rate on value or returned metres?",
            origin={"by": "model"},
        )
        per = SD(
            kind="metric",
            key="gm_per_metre",
            body={"name": "Gross margin per metre", "status": "needs_source", "needs": "x"},
            origin={"by": "model"},
            state="needs_source",
        )
        rows = [*drafts.stored(s), ratio, per]
        got = document.rule_changes(pages, rows, load_catalog(s), {})
    assert not [
        c for c in got if c.result == "mapped" and c.key in ("returns_rate", "gm_per_metre")
    ]
