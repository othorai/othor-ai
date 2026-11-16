# SPDX-License-Identifier: Apache-2.0
"""Versions, Undo and Restore (docs/03): every change is a version; nothing is ever deleted."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.ontology import service
from othor.ontology.models import OntologyRecord, OntologyVersion
from othor.ontology.types import ExprError, validate_body

A = {"term": "Revenue", "text": "net of GST"}
B = {"term": "Revenue", "text": "net of GST and returns"}


def _live(s):
    return {(r.type, r.key): r.body["text"] for r in service.active(s, ["vocabulary"])}


def test_apply_undo_restore(session_factory, two_companies):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        # setup: a draft, then Confirm = version 1
        assert (
            service.apply(s, [service.put("vocabulary", "revenue", A)], actor="u", reason="one")
            is None
        )
        assert service.active(s) == [] and len(service.current(s)) == 1
        v1 = service.confirm(s, actor="u")
        v2 = service.apply(
            s,
            [
                service.put("vocabulary", "revenue", B),
                service.put("vocabulary", "customer", {"term": "Customer", "text": "x"}),
            ],
            actor="u",
            reason="two",
        )
        assert (v1.number, v2.number) == (1, 2)
        assert {c["op"] for c in v2.diff["changes"]} == {"add", "change"}
        # unchanged → no new version
        assert (
            service.apply(s, [service.put("vocabulary", "revenue", B)], actor="u", reason="same")
            is None
        )
        v3 = service.apply(s, [service.retire("vocabulary", "customer")], actor="u", reason="three")
        assert _live(s) == {("vocabulary", "revenue"): "net of GST and returns"}
        v4 = service.undo(s, v3.number, actor="u")
        assert v4.number == 4
        assert _live(s) == {
            ("vocabulary", "revenue"): "net of GST and returns",
            ("vocabulary", "customer"): "x",
        }
        v5 = service.restore(s, 1, actor="u")
        assert v5.number == 5 and _live(s) == {("vocabulary", "revenue"): "net of GST"}
        # history is kept: state at v2 is still readable
        at2 = {(r.type, r.key): r.body["text"] for r in service.active_at(s, 2)}
        assert at2 == {
            ("vocabulary", "revenue"): "net of GST and returns",
            ("vocabulary", "customer"): "x",
        }
        # never deleted
        assert s.scalar(sa.select(sa.func.count()).select_from(OntologyRecord)) == 5
        assert s.scalar(sa.select(sa.func.count()).select_from(OntologyVersion)) == 5
        actions = [e.action for e in s.scalars(sa.select(Event).order_by(Event.at))]
        assert actions.count("ontology.version_created") == 4
        assert {
            "ontology.draft_changed",
            "ontology.confirmed",
            "ontology.undone",
            "ontology.restored",
        } <= set(actions)
        s.commit()


def test_setup_is_drafts_and_confirm_is_version_1(session_factory, two_companies):
    """However many scans and changes happen in setup, Confirm is v1; the next rescan is v2."""
    from othor.connectors.models import Source, SourceTable
    from othor.jobs.scan_job import _record_uses
    from tests.fixtures.sample_company.catalog import catalog_from_generator
    from tests.fixtures.sample_company.generate import generate

    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, generate())
        src = s.scalars(sa.select(Source).where(Source.name == "sales_dw")).one()
        for _ in range(3):  # three scans before Confirm
            _record_uses(s, src, uuid.uuid4())
        t = s.scalars(sa.select(SourceTable).where(SourceTable.name == "fact_returns")).one()
        t.use = "ignored"  # a scan sees something different
        _record_uses(s, src, uuid.uuid4())
        assert service.history(s) == []
        assert len(service.setup_trail(s)) == 2  # the first scan and the changed one
        assert service.confirm(s, actor="u").number == 1
        assert {r.status for r in service.current(s)} == {"active"}
        t.use = "metric_source"  # the first rescan after Confirm changes a use
        _record_uses(s, src, uuid.uuid4())
        assert service.current_number(s) == 2
        with pytest.raises(service.AlreadyConfirmed):
            service.confirm(s, actor="u")
        s.commit()


def test_one_key_twice_in_a_version_is_refused(session_factory, two_companies):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid), pytest.raises(ValueError, match="twice"):
        service.apply(
            s,
            [service.put("vocabulary", "r", A), service.put("vocabulary", "r", B)],
            actor="u",
            reason="x",
        )


@pytest.mark.parametrize(
    ("type_", "body", "msg"),
    [
        (
            "metric",
            {
                "name": "x",
                "formula": {"metric": "a"},
                "table": {"source": "s", "schema": "s", "table": "t"},
            },
            "either a base metric",
        ),
        (
            "metric",
            {
                "name": "x",
                "table": {"source": "s", "schema": "s", "table": "t"},
                "measure": {"agg": "sum", "expr": {"col": "a"}},
            },
            "business date",
        ),
        (
            "metric",
            {
                "name": "x",
                "table": {"source": "s", "schema": "s", "table": "t"},
                "date": "d",
                "measure": {"agg": "sum", "expr": {"sql": "DROP TABLE x"}},
            },
            "unknown expression",
        ),
        (
            "metric",
            {
                "name": "x",
                "table": {"source": "s", "schema": "s", "table": "t"},
                "date": "d",
                "measure": {"agg": "sum", "expr": {"col": "a; drop"}},
            },
            "characters",
        ),
        (
            "rule",
            {
                "kind": "unit_conversion",
                "table": {"source": "s", "schema": "s", "table": "t"},
                "column": "c",
                "when": {"lit": True},
                "factor": "abc",
            },
            "not a number",
        ),
        ("vocabulary", {"term": "p", "kind": "prefix_map", "entries": {"S%": "x"}}, "plain text"),
        ("nonsense", {}, "unknown record type"),
    ],
)
def test_bodies_are_validated(type_, body, msg):
    with pytest.raises((ValueError, ExprError), match=msg):
        validate_body(type_, body)
