# SPDX-License-Identifier: Apache-2.0
"""Delivery records (docs/03 "Briefs and delivery", M9).

- `delivery`: one row per email sent: a brief issue to a recipient (`ref` is unique, so a
  retried job never sends twice), a new link, or an owner alert / digest (`ref`, deduped by the
  job). `provider_id` is the relay's message id (Postmark's MessageID), so a bounce webhook can
  find its row. Bodies are never stored.
- `link_token`: a signed link (docs/02 "Security"). The token is 32 random bytes shown once in
  the URL; only its HMAC under the company's subkey is stored. Bound to one recipient, one
  brief issue (open) and one purpose; it never reads another issue from the URL.
- `postmark_event`: webhook records already applied (idempotency).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from othor.core.models import Base, IdMixin, TenantMixin

OPEN = "open"
UNSUBSCRIBE = "unsubscribe"


class Delivery(IdMixin, TenantMixin, Base):
    __tablename__ = "delivery"
    __table_args__ = (sa.UniqueConstraint("company_id", "ref"),)

    kind: Mapped[str] = mapped_column(sa.String(16))  # brief · link · alert · digest
    brief_issue_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("brief_issue.id"), index=True
    )
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("recipient.id"))
    # brief:<issue>:<recipient> · link:<issue>:<recipient>:<n> · alert:run_failed:<run> · …
    ref: Mapped[str] = mapped_column(sa.String(160))
    status: Mapped[str] = mapped_column(sa.String(16), default="queued")
    # queued · sent · failed · bounced
    sent_at: Mapped[datetime | None] = mapped_column()
    provider_id: Mapped[str | None] = mapped_column(sa.String(200), index=True)
    error: Mapped[str | None] = mapped_column(sa.String(300))
    link_token_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)


class LinkToken(IdMixin, TenantMixin, Base):
    __tablename__ = "link_token"

    recipient_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("recipient.id"), index=True)
    brief_issue_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("brief_issue.id"))
    purpose: Mapped[str] = mapped_column(sa.String(16))  # open · unsubscribe
    hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column()
    used_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()


class PostmarkEvent(IdMixin, TenantMixin, Base):
    __tablename__ = "postmark_event"

    record_id: Mapped[str] = mapped_column(sa.String(80), unique=True)
    kind: Mapped[str] = mapped_column(sa.String(32))
    delivery_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
