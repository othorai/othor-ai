# SPDX-License-Identifier: Apache-2.0
"""The owner's snoozes and mutes (M6b, docs/04 §4). Each action writes its row and an event;
Undo ends the row (never deleted). The pages apply them at once through
`findings.overlay`; the run reads them as inputs (`orchestrator.active_holds`) from the next
night. Inside the company's tenant scope."""

from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from othor.core.events import record_event
from othor.engine.findings import pattern_of
from othor.engine.models import Mute, Snooze, Watch

SNOOZE_DAYS = 7


def _label(f: dict[str, Any]) -> str:
    return str(f.get("title") or f.get("metric") or "")[:300]


def snooze(s: Any, f: dict[str, Any], *, actor: str, today: dt.date) -> Snooze:
    until = today + dt.timedelta(days=SNOOZE_DAYS)
    row = Snooze(fingerprint=f["fingerprint"], until=until, label=_label(f), actor=actor)
    s.add(row)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="finding.snoozed",
        object_type="finding",
        object_id=row.id,
        after={"fingerprint": f["fingerprint"], "until": until.isoformat()},
    )
    return row


def mute(s: Any, f: dict[str, Any], *, actor: str) -> Mute:
    row = Mute(pattern=pattern_of(f), fingerprint=f["fingerprint"], label=_label(f), actor=actor)
    s.add(row)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="finding.muted",
        object_type="finding",
        object_id=row.id,
        after={"fingerprint": f["fingerprint"], "pattern": row.pattern},
    )
    return row


def undo(s: Any, fingerprint: str, *, actor: str, now: datetime) -> int:
    """End this finding's snoozes and mutes. Returns how many were ended."""
    rows: list[Any] = [
        *s.scalars(
            sa.select(Snooze).where(Snooze.fingerprint == fingerprint, Snooze.ended_at.is_(None))
        ),
        *s.scalars(sa.select(Mute).where(Mute.fingerprint == fingerprint, Mute.ended_at.is_(None))),
    ]
    for row in rows:
        row.ended_at = now
        record_event(
            s,
            actor=actor,
            action="finding.unsnoozed" if isinstance(row, Snooze) else "finding.unmuted",
            object_type="finding",
            object_id=row.id,
            before={"fingerprint": fingerprint},
            after={"ended": now.isoformat()},
        )
    return len(rows)


def active(s: Any, today: dt.date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(snoozes, mutes) active now, as plain data for `findings.overlay`."""
    sn = s.scalars(sa.select(Snooze).where(Snooze.ended_at.is_(None), Snooze.until >= today)).all()
    mu = s.scalars(sa.select(Mute).where(Mute.ended_at.is_(None))).all()
    return (
        [{"fingerprint": x.fingerprint, "until": x.until.isoformat()} for x in sn],
        [{"pattern": x.pattern, "fingerprint": x.fingerprint} for x in mu],
    )


def stop_watch(s: Any, watch_id: Any, *, actor: str, now: datetime) -> Watch | None:
    w: Watch | None = s.scalars(
        sa.select(Watch).where(Watch.id == watch_id, Watch.stopped_at.is_(None))
    ).first()
    if w is None:
        return None
    w.stopped_at = now
    record_event(
        s,
        actor=actor,
        action="watch.stopped",
        object_type="watch",
        object_id=w.id,
        before={"label": w.label, "status": w.status},
        after={"stopped": now.isoformat()},
    )
    return w
