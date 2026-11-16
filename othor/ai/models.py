# SPDX-License-Identifier: Apache-2.0
"""Every model call, logged (docs/03 "AI log", docs/06 "Gateway")."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from othor.core.models import Base, IdMixin, TenantMixin


class ModelCall(IdMixin, TenantMixin, Base):
    __tablename__ = "model_call"
    __table_args__ = (
        sa.Index("ix_model_call_cache", "company_id", "prompt_id", "input_hash"),
        sa.Index("ix_model_call_created", "company_id", "created_at"),  # AI usage (M15.8)
    )

    prompt_id: Mapped[str] = mapped_column(sa.String(64))
    prompt_version: Mapped[str] = mapped_column(sa.String(16))
    provider: Mapped[str] = mapped_column(sa.String(32))
    model_id: Mapped[str] = mapped_column(sa.String(64))
    input_hash: Mapped[str] = mapped_column(sa.String(64))
    input: Mapped[dict[str, Any]] = mapped_column()  # already redacted by the guards
    output: Mapped[str | None] = mapped_column(sa.Text)
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    latency_ms: Mapped[int] = mapped_column(default=0)
    guard_result: Mapped[str] = mapped_column(sa.String(32))  # passed · failed:<why> · error
    cached: Mapped[bool] = mapped_column(default=False)
