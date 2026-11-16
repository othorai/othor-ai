# SPDX-License-Identifier: Apache-2.0
"""The job table: idempotent, resumable work items claimed by the worker (docs/02)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from othor.core.models import Base, IdMixin, TenantMixin

MAX_ATTEMPTS = 3  # a job past it never runs again (read by the runner and Run again now)


class Job(IdMixin, TenantMixin, Base):
    __tablename__ = "job"
    __table_args__ = (
        sa.Index("ix_job_claim", "status", "run_after"),
        sa.UniqueConstraint("company_id", "dedupe_key"),
    )

    kind: Mapped[str] = mapped_column(sa.String(32))  # scan · read · label
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(
        sa.String(16), default="queued"
    )  # queued running done failed
    attempts: Mapped[int] = mapped_column(default=0)
    run_after: Mapped[datetime | None] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column()
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(sa.String(300))
    dedupe_key: Mapped[str | None] = mapped_column(sa.String(80))  # e.g. nightly:2026-11-02


class WorkerHeartbeat(Base):
    """One row (id 1): when the worker's scheduler last ticked (M11b §6, after #16). No company
    data, so no company_id, like the job table's system rows."""

    __tablename__ = "worker_heartbeat"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    at: Mapped[datetime] = mapped_column()
