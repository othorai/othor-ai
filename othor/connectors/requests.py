# SPDX-License-Identifier: Apache-2.0
"""Request a connector (M15.4, owner 13 Nov 2026): from Add a source, in the app only. Stored per
company with an event, emailed to support, counted across companies (`othor admin requests`)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from othor.connectors.models import REACH, ConnectorRequest
from othor.core.events import record_event


def clean_system(text: str) -> str:
    return " ".join((text or "").split())[:120]


def record(
    s: Session, *, system: str, version: str, wants: str, reach: str, actor: str
) -> ConnectorRequest:
    """One request; raises ValueError when the system isn't named (the only required field)."""
    name = clean_system(system)
    if not name:
        raise ValueError("Name the system you use.")
    row = ConnectorRequest(
        system=name,
        version=" ".join((version or "").split())[:60] or None,
        wants=(wants or "").strip()[:2000] or None,
        reach=reach if reach in REACH else None,
        requested_by=actor,
    )
    s.add(row)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="connector.requested",
        object_type="connector_request",
        object_id=row.id,
        after={"system": name, "reach": row.reach},
    )
    return row
