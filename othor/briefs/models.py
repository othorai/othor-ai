# SPDX-License-Identifier: Apache-2.0
"""Briefs and their issues (docs/03 "Briefs and delivery", M8).

- `brief` and `brief_version`: a brief's definition. Every change saves a version (with an
  event); the nightly run reads the active versions into its context file, so a change applies
  from the next run and replay sees the same inputs.
- `brief_issue`: what one run made for one brief, written at seal from the run's outputs
  (`briefs.json`). The rewrite of its prose, if any, is written later, outside the run.
- `recipient`: who gets it; M9 sends it (`othor.mail`). `suggested_brief`: drafted questions (4b).
"""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from othor.core.models import Base, IdMixin, TenantMixin


class Brief(IdMixin, TenantMixin, Base):
    __tablename__ = "brief"

    name: Mapped[str] = mapped_column(sa.String(120))
    status: Mapped[str] = mapped_column(sa.String(16), default="active")  # active · archived
    position: Mapped[int] = mapped_column(default=0)
    is_default: Mapped[bool] = mapped_column(default=False)
    current_version: Mapped[int] = mapped_column(default=1)
    deleted_at: Mapped[datetime | None] = mapped_column()


class BriefVersion(IdMixin, TenantMixin, Base):
    __tablename__ = "brief_version"
    __table_args__ = (sa.UniqueConstraint("brief_id", "number"),)

    brief_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("brief.id"), index=True)
    number: Mapped[int] = mapped_column()
    body: Mapped[dict[str, Any]] = mapped_column()  # question, scope, headline, ask_owner, …
    diff: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by: Mapped[str] = mapped_column(sa.String(120))
    reason: Mapped[str] = mapped_column(sa.String(300), default="")


class BriefIssue(IdMixin, TenantMixin, Base):
    __tablename__ = "brief_issue"
    __table_args__ = (sa.UniqueConstraint("brief_id", "run_id"),)

    brief_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("brief.id"), index=True)
    brief_version: Mapped[int] = mapped_column()
    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("run.id"), index=True)
    date: Mapped[dt.date] = mapped_column(sa.Date, index=True)
    status: Mapped[str] = mapped_column(sa.String(16))  # ready · failed · rerun
    title: Mapped[str] = mapped_column(sa.Text, default="")
    quiet: Mapped[bool] = mapped_column(default=False)
    body: Mapped[dict[str, Any]] = mapped_column()  # the issue as in the run's outputs
    issue_hash: Mapped[str] = mapped_column(sa.String(64))
    prose: Mapped[dict[str, Any]] = mapped_column(default=dict)  # template · model text · used


class Recipient(IdMixin, TenantMixin, Base):
    __tablename__ = "recipient"
    __table_args__ = (sa.UniqueConstraint("brief_id", "email"),)

    brief_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("brief.id"), index=True)
    email: Mapped[str] = mapped_column(sa.String(320))
    name: Mapped[str] = mapped_column(sa.String(120), default="")
    role: Mapped[str] = mapped_column(sa.String(120), default="")
    # active · unsubscribed · removed · bounced
    status: Mapped[str] = mapped_column(sa.String(16), default="active")
    added_by: Mapped[str] = mapped_column(sa.String(120))
    unsubscribed_at: Mapped[datetime | None] = mapped_column()  # 10f; bounced_at for a bounce
    bounced_at: Mapped[datetime | None] = mapped_column()


class SuggestedBrief(IdMixin, TenantMixin, Base):
    __tablename__ = "suggested_brief"

    question: Mapped[str] = mapped_column(sa.Text)
    name: Mapped[str] = mapped_column(sa.String(120))
    draft: Mapped[dict[str, Any]] = mapped_column()
    evidence: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    status: Mapped[str] = mapped_column(sa.String(16), default="shown")  # created · dismissed
    prompt_version: Mapped[str] = mapped_column(sa.String(16), default="")


class BigPicture(IdMixin, TenantMixin, Base):
    """Big Picture (v0.19, M19.5): one report per run and language, written by Othor AI from the
    run's computed results; every number in it a slot filled by code (othor.briefs.big_picture).
    Made on demand after seal, never by the run; stored so it's made once."""

    __tablename__ = "big_picture"
    __table_args__ = (sa.UniqueConstraint("company_id", "run_id", "language"),)

    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("run.id"), index=True)
    language: Mapped[str] = mapped_column(sa.String(8))  # en · hi
    body: Mapped[dict[str, Any]] = mapped_column()  # filled sections, charts, sources
    prompt: Mapped[str] = mapped_column(sa.String(64))  # big_picture@v1
    model_id: Mapped[str] = mapped_column(sa.String(120), default="")
    input_hash: Mapped[str] = mapped_column(sa.String(64))
    guard: Mapped[str] = mapped_column(sa.String(32))  # passed · dropped:N
    deleted_at: Mapped[datetime | None] = mapped_column()
