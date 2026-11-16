# SPDX-License-Identifier: Apache-2.0
"""`table_use` Ontology records: every use change, by the scan or the owner (docs/05)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ontology import service
from othor.ontology.models import OntologyRecord


def key_for(source_id: Any, stable_key: str) -> str:
    return f"{source_id}:{stable_key}"


def current(s: Session, source_id: Any, stable_key: str) -> OntologyRecord | None:
    return s.scalars(
        sa.select(OntologyRecord).where(
            OntologyRecord.type == "table_use",
            OntologyRecord.key == key_for(source_id, stable_key),
            OntologyRecord.status.in_(("active", "draft")),
        )
    ).first()


def change(
    *, source_id: Any, stable_key: str, body: dict[str, Any], source_ref: dict[str, Any]
) -> service.Change:
    """A table-use change to batch into one version (a scan's defaults are one version)."""
    return service.put("table_use", key_for(source_id, stable_key), body, source_ref)


def write(
    s: Session,
    *,
    source_id: Any,
    stable_key: str,
    body: dict[str, Any],
    actor: str,
    source_ref: dict[str, Any],
    reason: str | None = None,
) -> OntologyRecord:
    """One owner change = one Ontology version. Returns the live record."""
    c = change(source_id=source_id, stable_key=stable_key, body=body, source_ref=source_ref)
    service.apply(
        s,
        [c],
        actor=actor,
        reason=reason or f"Use of {body.get('schema')}.{body.get('table')}: {body.get('use')}",
        source_ref=source_ref,
    )
    rec = current(s, source_id, stable_key)
    assert rec is not None
    return rec
