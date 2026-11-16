# SPDX-License-Identifier: Apache-2.0
"""Uploads, the business message, setup drafts and chats (docs/03 "Uploads and setup", "Home")."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from othor.core.models import Base, IdMixin, TenantMixin, utcnow


class Upload(IdMixin, TenantMixin, Base):
    """A file the owner dropped. Stored encrypted under the company's uploads folder."""

    __tablename__ = "upload"
    __table_args__ = (sa.UniqueConstraint("company_id", "sha256"),)

    filename: Mapped[str] = mapped_column(sa.String(300))
    kind: Mapped[str] = mapped_column(sa.String(16))  # xlsx csv sql pdf image text other
    content_type: Mapped[str] = mapped_column(sa.String(100))
    size: Mapped[int] = mapped_column(sa.BigInteger)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    storage_path: Mapped[str] = mapped_column(sa.String(500))
    # added · reading · read · read_as_text · needs_model · unreadable
    status: Mapped[str] = mapped_column(sa.String(16), default="added")
    read_by: Mapped[str | None] = mapped_column(sa.String(8))  # code · text · model
    summary: Mapped[dict[str, Any] | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(sa.String(300))
    uploaded_by: Mapped[str] = mapped_column(sa.String(64))
    deleted_at: Mapped[datetime | None] = mapped_column()


class UploadRef(IdMixin, TenantMixin, Base):
    """A citable piece of an upload: 'tab Sales!D1', 'line 12', 'p. 7', 'slide 4'."""

    __tablename__ = "upload_ref"

    upload_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("upload.id"), index=True)
    locator: Mapped[str] = mapped_column(sa.String(100))
    # column · target · definition · term · name · table · filter · page · dimension · metric
    kind: Mapped[str] = mapped_column(sa.String(16))
    text: Mapped[str] = mapped_column(sa.Text)
    data: Mapped[dict[str, Any] | None] = mapped_column()


class BusinessMessage(IdMixin, TenantMixin, Base):
    __tablename__ = "business_message"

    version: Mapped[int] = mapped_column(default=1)
    text: Mapped[str] = mapped_column(sa.Text, default="")
    draft_text: Mapped[str | None] = mapped_column(sa.Text)
    not_covered: Mapped[list[str] | None] = mapped_column(sa.JSON)
    data_lives: Mapped[list[str] | None] = mapped_column(sa.JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class SetupDraft(IdMixin, TenantMixin, Base):
    """A drafted metric (or goal, rule, pattern…) before Confirm. Not live; never compiled into
    a stored spec. `value` is computed by SQL; `body` is a typed Ontology body."""

    __tablename__ = "setup_draft"
    __table_args__ = (sa.UniqueConstraint("company_id", "kind", "key"),)

    kind: Mapped[str] = mapped_column(
        sa.String(16)
    )  # metric goal business_fact rule vocabulary known_pattern
    key: Mapped[str] = mapped_column(sa.String(100))
    draft_no: Mapped[int] = mapped_column(default=1)
    body: Mapped[dict[str, Any]] = mapped_column()
    prev_body: Mapped[dict[str, Any] | None] = mapped_column()  # for the redraft diff
    origin: Mapped[dict[str, Any]] = mapped_column(
        default=dict
    )  # by model|rules|owner, prompt, rule
    refs: Mapped[list[dict[str, Any]] | None] = mapped_column(sa.JSON)
    confidence: Mapped[str] = mapped_column(sa.String(8), default="low")  # high check conflict low
    # needs_confirmation · confirmed · needs_source · excluded_table · skipped
    state: Mapped[str] = mapped_column(sa.String(24), default="needs_confirmation")
    note: Mapped[str | None] = mapped_column(sa.String(500))
    value: Mapped[dict[str, Any] | None] = mapped_column()
    changed: Mapped[bool] = mapped_column(default=False)
    frozen: Mapped[bool] = mapped_column(default=False)
    position: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ChatThread(IdMixin, TenantMixin, Base):
    __tablename__ = "chat_thread"

    purpose: Mapped[str] = mapped_column(sa.String(32))  # conflict where connect edit fix_tab
    subject: Mapped[str] = mapped_column(sa.String(200))  # e.g. "metric:net_revenue"
    status: Mapped[str] = mapped_column(sa.String(16), default="open")  # open applied closed


class ChatMessage(IdMixin, TenantMixin, Base):
    __tablename__ = "chat_message"

    thread_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("chat_thread.id"), index=True)
    role: Mapped[str] = mapped_column(sa.String(8))  # user · ai · system
    text: Mapped[str] = mapped_column(sa.Text)
    proposal: Mapped[dict[str, Any] | None] = mapped_column()  # with the computed value
    applied: Mapped[bool] = mapped_column(default=False)


class SetupChange(IdMixin, TenantMixin, Base):
    """What an uploaded document changed in the drafts (2ne), cited by its quote (2nf)."""

    __tablename__ = "setup_change"

    upload_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    draft_round: Mapped[int] = mapped_column(default=2)
    subject: Mapped[str] = mapped_column(sa.String(120))  # a metric's or dimension's name
    draft_key: Mapped[str | None] = mapped_column(sa.String(100))
    quote: Mapped[str | None] = mapped_column(sa.String(500))
    locator: Mapped[str | None] = mapped_column(sa.String(80))  # p. 7
    # conflict_resolved mapped rule_added dimension_updated filter_added noted no_change
    result: Mapped[str] = mapped_column(sa.String(24))
    applied: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[str] = mapped_column(sa.String(64))
