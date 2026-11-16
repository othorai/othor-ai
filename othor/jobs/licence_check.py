# SPDX-License-Identifier: Apache-2.0
"""The licence, checked once a minute by the worker (M20.3): when its state changes (valid → grace
→ expired, a new key, a key gone), one instance-level event says so. No network; cheap (a file
read and one signature check)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core import licence
from othor.core.events import SYSTEM_ACTOR, record_platform_event
from othor.core.models import PlatformEvent
from othor.core.tenancy import system_scope


def check(sf: sessionmaker[Session], settings: Any, now: dt.datetime) -> str | None:
    """Record the licence's state if it changed; returns the new state, or None if unchanged."""
    st = licence.status(settings, now.date())
    with sf() as s, system_scope("worker: the licence's state"):
        last = s.scalars(
            sa.select(PlatformEvent)
            .where(PlatformEvent.action == "licence.state")
            .order_by(PlatformEvent.at.desc())
            .limit(1)
        ).first()
        was = (last.after or {}) if last is not None else {}
        if was.get("state", "none") == st.state and was.get("id", "") == st.id:
            return None
        record_platform_event(
            s,
            actor=SYSTEM_ACTOR,
            action="licence.state",
            object_type="licence",
            object_id=st.id or "none",
            before={"state": was.get("state", "none")},
            after={
                "state": st.state,
                "id": st.id,
                "customer": st.customer,
                "reason": st.reason,
                "expires": st.expires.isoformat() if st.expires else None,
            },
            at=now,
        )
        s.commit()
    return st.state
