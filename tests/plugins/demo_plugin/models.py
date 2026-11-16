# SPDX-License-Identifier: Apache-2.0
"""The demo plug-in's own table, on its own migration branch."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from othor.core.models import Base, IdMixin, TenantMixin


class DemoNote(IdMixin, TenantMixin, Base):
    __tablename__ = "demo_note"
    text: Mapped[str] = mapped_column(sa.String(200))
