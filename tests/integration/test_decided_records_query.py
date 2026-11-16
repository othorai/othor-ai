# SPDX-License-Identifier: Apache-2.0
"""M15.1: Othor AI's decided records are found in the database (a JSON path on source_ref) rather
than by loading every live record; the query must hold on SQLite and PostgreSQL alike, and stay
inside the company."""

from __future__ import annotations

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.home.answers import DECIDED_BY, decided_records
from othor.ontology.models import OntologyRecord


def _rec(cid, key, ref, status="active"):
    return OntologyRecord(
        company_id=cid,
        workspace_id=cid,
        type="rule",
        key=key,
        body={},
        status=status,
        source_ref=ref,
        created_by="test",
    )


def test_only_live_records_othor_ai_decided_in_this_company(session_factory, two_companies):
    a, b = two_companies
    with session_factory() as s, tenant_scope(a):
        s.add_all(
            [
                _rec(a, "decided", {"kind": "answer", "decided_by": DECIDED_BY}),
                _rec(a, "owner", {"kind": "answer"}),
                _rec(a, "no-ref", None),
                _rec(a, "retired", {"decided_by": DECIDED_BY}, status="retired"),
            ]
        )
        s.commit()
    with session_factory() as s, tenant_scope(b):
        s.add(_rec(b, "other-company", {"decided_by": DECIDED_BY}))
        s.commit()
    with session_factory() as s, tenant_scope(a):
        assert [r.key for r in decided_records(s)] == ["decided"]
        assert s.scalar(sa.select(sa.func.count()).select_from(OntologyRecord)) == 4
