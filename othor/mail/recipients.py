# SPDX-License-Identifier: Apache-2.0
"""A recipient leaving or rejoining a brief (10f) and the bounce/complaint webhook. Every change
is an event; the owner sees it on Edit brief · Delivery and (M10) on Home (decided 6 Nov 2026:
no email to the owner)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.briefs.models import Recipient
from othor.core.events import record_event
from othor.mail.delivery import mark_bounced
from othor.mail.models import Delivery, PostmarkEvent

BOUNCES = {"HardBounce", "BadEmailAddress", "ManuallyDeactivated"}


def actor(r: Recipient) -> str:
    return f"recipient:{r.id}"


def unsubscribe(s: Session, r: Recipient, now: dt.datetime, *, via: str) -> bool:
    """Off this brief from the next delivery. False when they already were."""
    if r.status != "active":
        return False
    r.status, r.unsubscribed_at = "unsubscribed", now
    record_event(
        s,
        actor=actor(r),
        action="recipient.unsubscribed",
        object_type="recipient",
        object_id=r.id,
        before={"status": "active"},
        after={"status": "unsubscribed", "brief": r.brief_id, "email": r.email, "via": via},
        at=now,
    )
    return True


def resubscribe(s: Session, r: Recipient, now: dt.datetime) -> bool:
    """Undo (10f): back on the brief. Only an unsubscribe can be undone this way; a removed or
    bounced recipient is added back by the owner."""
    if r.status != "unsubscribed":
        return False
    r.status, r.unsubscribed_at = "active", None
    record_event(
        s,
        actor=actor(r),
        action="recipient.resubscribed",
        object_type="recipient",
        object_id=r.id,
        before={"status": "unsubscribed"},
        after={
            "status": "active",
            "brief": r.brief_id,
            "email": r.email,
            "undo_of": "unsubscribed",
        },
        at=now,
    )
    return True


def postmark_record(
    s: Session, delivery: Delivery, record: dict[str, Any], now: dt.datetime
) -> str:
    """Apply one webhook record to its delivery (inside the delivery's company scope). Returns
    what happened: duplicate · bounced · unsubscribed · ignored."""
    kind = str(record.get("RecordType") or "")
    rid = f"{kind}:{record.get('ID') or record.get('RecordID') or ''}"
    if s.scalars(sa.select(PostmarkEvent.id).where(PostmarkEvent.record_id == rid)).first():
        return "duplicate"
    s.add(PostmarkEvent(record_id=rid[:80], kind=kind[:32], delivery_id=delivery.id))
    rec = (
        s.scalars(sa.select(Recipient).where(Recipient.id == delivery.recipient_id)).first()
        if delivery.recipient_id
        else None
    )
    if kind == "Bounce" and str(record.get("Type") or "") in BOUNCES:
        delivery.status, delivery.error = "bounced", f"postmark {record.get('Type')}"
        if rec is not None:
            mark_bounced(s, rec, now, reason=str(record.get("Type")))
        return "bounced"
    if kind == "SpamComplaint" and rec is not None:
        unsubscribe(s, rec, now, via="complaint")
        return "unsubscribed"
    return "ignored"
