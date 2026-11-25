# SPDX-License-Identifier: Apache-2.0
"""Every model call, logged (docs/03 "AI log", docs/06 "Gateway")."""

from __future__ import annotations

from datetime import datetime
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
    # the company's own model (Settings › AI Model, its key or its own server): shown on AI usage,
    # outside the monthly allowance (owner, 25 Nov 2026)
    own_key: Mapped[bool] = mapped_column(default=False, server_default=sa.false())


class ModelSetting(IdMixin, TenantMixin, Base):
    """A company's own model (Any model, 0.37): for all kinds of work (`kind` = "all") or one
    kind (understanding · reports · answering), which overrides "all". No row: the install's
    model (env). The key is encrypted with the instance key, bound to the company and provider;
    it is never shown again, logged or exported. A row is soft-deleted when replaced."""

    __tablename__ = "model_setting"
    __table_args__ = (sa.Index("ix_model_setting_kind", "company_id", "kind"),)

    kind: Mapped[str] = mapped_column(sa.String(16))
    provider: Mapped[str] = mapped_column(sa.String(32))  # a preset key (othor.ai.providers)
    base_url: Mapped[str] = mapped_column(sa.String(300), default="")
    model_id: Mapped[str] = mapped_column(sa.String(120), default="")
    key_ciphertext: Mapped[bytes | None] = mapped_column(sa.LargeBinary)
    set_by: Mapped[str] = mapped_column(sa.String(64), default="")
    # the last "Check this model": ok · failed, and when (the words are rebuilt from these)
    check_result: Mapped[str | None] = mapped_column(sa.String(32))
    checked_at: Mapped[datetime | None] = mapped_column()
    deleted_at: Mapped[datetime | None] = mapped_column()
