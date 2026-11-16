# SPDX-License-Identifier: Apache-2.0
"""Write rows to the append-only event log (actor, time, object, before, after)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from othor.core.models import Event, PlatformEvent, utcnow
from othor.core.tenancy import current_company_id

SYSTEM_ACTOR = "system"


def _jsonable(v: Any) -> Any:
    if isinstance(v, Mapping):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, uuid.UUID | Decimal):
        return str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


def record_event(
    session: Session,
    *,
    actor: str | uuid.UUID,
    action: str,
    object_type: str,
    object_id: str | uuid.UUID,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    at: datetime | None = None,
) -> Event:
    cid = current_company_id()
    ev = Event(
        company_id=cid,
        workspace_id=cid,
        actor=str(actor),
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        before=_jsonable(before) if before is not None else None,
        after=_jsonable(after) if after is not None else None,
        at=at or utcnow(),
    )
    session.add(ev)
    session.flush()
    return ev


def record_platform_event(
    session: Session,
    *,
    actor: str | uuid.UUID,
    action: str,
    object_type: str,
    object_id: str | uuid.UUID,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    at: datetime | None = None,
) -> PlatformEvent:
    """An instance-level event that belongs to no company yet (M11d part C): invitations and
    requests for access. Append-only, like `event`."""
    ev = PlatformEvent(
        actor=str(actor),
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        before=_jsonable(before) if before is not None else None,
        after=_jsonable(after) if after is not None else None,
        at=at or utcnow(),
    )
    session.add(ev)
    session.flush()
    return ev
