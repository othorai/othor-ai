# SPDX-License-Identifier: Apache-2.0
"""The demo plug-in's table (its own branch, label `demo`)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0001_demo_note"
down_revision = None
branch_labels = ("demo",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_note",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.String(200), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("demo_note")
