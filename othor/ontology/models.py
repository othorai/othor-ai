# SPDX-License-Identifier: Apache-2.0
"""Typed Ontology records (docs/03 "Ontology"). M2 writes `table_use` records only; M3 adds
versions and the other record types on the same table."""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from othor.core.models import AppendOnly, Base, IdMixin, TenantMixin


class OntologyRecord(IdMixin, TenantMixin, Base):
    __tablename__ = "ontology_record"
    __table_args__ = (sa.Index("ix_ontology_record_key", "company_id", "type", "key", "status"),)

    type: Mapped[str] = mapped_column(sa.String(32))
    key: Mapped[str] = mapped_column(sa.String(200))
    body: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(sa.String(16), default="active")  # draft active retired
    source_ref: Mapped[dict[str, Any] | None] = mapped_column()
    created_by: Mapped[str] = mapped_column(sa.String(64))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    version_from: Mapped[int | None] = mapped_column()
    version_to: Mapped[int | None] = mapped_column()


class OntologyVersion(IdMixin, TenantMixin, Base):
    """Every Apply / Accept / Undo / Restore creates one (docs/03). Never updated."""

    __tablename__ = "ontology_version"
    __table_args__ = (sa.UniqueConstraint("company_id", "number"),)

    number: Mapped[int] = mapped_column()
    created_by: Mapped[str] = mapped_column(sa.String(64))
    reason: Mapped[str] = mapped_column(sa.String(300))
    source_ref: Mapped[dict[str, Any] | None] = mapped_column()
    diff: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # M10b: the records hash (its meaning without number, time, author, reason or citations)
    content_hash: Mapped[str | None] = mapped_column(sa.String(64))


class Spec(AppendOnly, IdMixin, TenantMixin, Base):
    """The compiled, immutable artefact the nightly run executes (docs/04 §1). Append-only."""

    __tablename__ = "spec"
    # one spec per Ontology version and compiler: a new image compiles its own, old runs keep theirs
    __table_args__ = (sa.UniqueConstraint("company_id", "ontology_version", "compiler_version"),)

    ontology_version: Mapped[int] = mapped_column()
    compiler_version: Mapped[str] = mapped_column(sa.String(32))
    compiled: Mapped[dict[str, Any]] = mapped_column()
    hash: Mapped[str] = mapped_column(sa.String(64), index=True)
    # M10b: the compiled document's meaning without its version number (null on specs written
    # before 0029: append-only, so read through `content.spec_content_hash`)
    content_hash: Mapped[str | None] = mapped_column(sa.String(64))


class Question(IdMixin, TenantMixin, Base):
    """A question for the owner on Home (docs/03 "Home"). Screens come in M10."""

    __tablename__ = "question"
    __table_args__ = (sa.UniqueConstraint("company_id", "fingerprint"),)

    text: Mapped[str] = mapped_column(sa.String(500))
    why: Mapped[str] = mapped_column(sa.String(500), default="")
    unblocks: Mapped[dict[str, Any] | None] = mapped_column()
    origin: Mapped[dict[str, Any]] = mapped_column(default=dict)
    quick_replies: Mapped[list[str] | None] = mapped_column(sa.JSON)
    status: Mapped[str] = mapped_column(sa.String(16), default="open")  # open answered skipped
    fingerprint: Mapped[str] = mapped_column(sa.String(64))
    answer_record_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    # M10a: what kind of question (the quick replies map by kind), what it is about, its place
    # in the queue, and the answer (the Ontology version it wrote, for Undo)
    kind: Mapped[str] = mapped_column(sa.String(32), default="other")
    subject: Mapped[dict[str, Any] | None] = mapped_column()
    rank_key: Mapped[str] = mapped_column(sa.String(80), default="")
    answer_version: Mapped[int | None] = mapped_column()
    answer_text: Mapped[str | None] = mapped_column(sa.String(500))
    answered_at: Mapped[datetime | None] = mapped_column()
    skipped_until: Mapped[dt.date | None] = mapped_column()
    # a typed answer drafted by the model, waiting for Apply: {changes, text} (0034)
    draft: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
