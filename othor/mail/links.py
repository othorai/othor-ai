# SPDX-License-Identifier: Apache-2.0
"""Signed links for recipients (docs/02 "Security", docs/09, 10d–10f).

A token is 32 random bytes, shown once in the email's URL. Only HMAC-SHA256(company subkey,
token) is stored (`link_token.hash`), so a database read can't open a brief. Each token is bound
to one recipient, one brief issue and one purpose:

- `open`: the read-only issue (10d) for 7 days. The page reads the issue from the token, never
  from the URL, so a link can't be reused for another brief, another day or another company.
- `unsubscribe`: one click (10f), for 60 days, so an older email still unsubscribes.

A removed recipient's links stop at once. An unsubscribed recipient's Open links run to their
expiry (they already have that issue). An expired Open link offers a new one (10e), sent to the
same address only, for an issue at most 30 days old, at most 3 a day.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.briefs.models import Brief, BriefIssue, Recipient
from othor.core.crypto import InstanceKey
from othor.mail.models import OPEN, UNSUBSCRIBE, Delivery, LinkToken

TTL = {OPEN: dt.timedelta(days=7), UNSUBSCRIBE: dt.timedelta(days=60)}
REISSUE_MAX_AGE = dt.timedelta(days=30)
REISSUE_PER_DAY = 3
SUBKEY = "signed-links"


def token_hash(key: InstanceKey, company_id: uuid.UUID, token: str) -> str:
    return hmac.new(key.subkey(company_id, SUBKEY), token.encode(), hashlib.sha256).hexdigest()


def issue(
    s: Session,
    key: InstanceKey,
    company_id: uuid.UUID,
    *,
    recipient_id: uuid.UUID,
    brief_issue_id: uuid.UUID,
    purpose: str,
    now: dt.datetime,
) -> tuple[str, LinkToken]:
    """A new token (returned once) and its stored row."""
    if purpose not in TTL:
        raise ValueError(f"unknown link purpose {purpose!r}")
    token = secrets.token_urlsafe(32)
    row = LinkToken(
        recipient_id=recipient_id,
        brief_issue_id=brief_issue_id,
        purpose=purpose,
        hash=token_hash(key, company_id, token),
        expires_at=now + TTL[purpose],
        created_at=now,
    )
    s.add(row)
    s.flush()
    return token, row


@dataclass
class Resolved:
    """A token found for this company and purpose. `state`: ok · expired · removed."""

    row: LinkToken
    recipient: Recipient
    issue: BriefIssue
    brief: Brief
    state: str

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def resolve(
    s: Session,
    key: InstanceKey,
    company_id: uuid.UUID,
    token: str,
    purpose: str,
    now: dt.datetime,
) -> Resolved | None:
    """The token's issue and recipient, or None when it isn't one of this company's tokens for
    this purpose (the caller shows the same 404 as any missing page). Runs inside the company's
    tenant scope, so another company's token can't match even with the same hash."""
    if not token or len(token) > 100:
        return None
    row = s.scalars(
        sa.select(LinkToken).where(LinkToken.hash == token_hash(key, company_id, token))
    ).first()
    if row is None or row.purpose != purpose:
        return None
    rec = s.scalars(sa.select(Recipient).where(Recipient.id == row.recipient_id)).one()
    iss = s.scalars(sa.select(BriefIssue).where(BriefIssue.id == row.brief_issue_id)).one()
    brief = s.scalars(sa.select(Brief).where(Brief.id == iss.brief_id)).one()
    if brief.deleted_at is not None or brief.status != "active":
        return None
    state = "ok"
    if row.revoked_at is not None or rec.status == "removed":
        state = "removed"
    elif row.expires_at <= now:
        state = "expired"
    return Resolved(row, rec, iss, brief, state)


def can_reissue(s: Session, r: Resolved, today: dt.date, now: dt.datetime) -> str | None:
    """None when a new link may be sent (10e), else the reason it may not."""
    if r.recipient.status != "active":
        return "inactive"
    if today - r.issue.date > REISSUE_MAX_AGE:
        return "too_old"
    sent = s.scalar(
        sa.select(sa.func.count())
        .select_from(Delivery)
        .where(
            Delivery.kind == "link",
            Delivery.recipient_id == r.recipient.id,
            Delivery.created_at > now - dt.timedelta(days=1),
        )
    )
    if int(sent or 0) >= REISSUE_PER_DAY:
        return "limit"
    return None


def revoke_for(s: Session, recipient_id: uuid.UUID, now: dt.datetime) -> int:
    """A removed recipient: every link they hold stops working."""
    rows = s.scalars(
        sa.select(LinkToken).where(
            LinkToken.recipient_id == recipient_id, LinkToken.revoked_at.is_(None)
        )
    ).all()
    for r in rows:
        r.revoked_at = now
    return len(rows)
