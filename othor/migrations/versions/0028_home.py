# SPDX-License-Identifier: Apache-2.0
"""home: proposal decisions (who, the version written, undo) and question kind, subject,
answer version and skip (M10a)

Revision ID: 0028_home
Revises: 0027_delivery
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_home"
down_revision = "0027_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("proposal", schema=None) as b:
        b.add_column(sa.Column("decided_by", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("applied_version", sa.Integer(), nullable=True))
        b.add_column(sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("question", schema=None) as b:
        b.add_column(
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="other")
        )
        b.add_column(sa.Column("subject", sa.JSON(), nullable=True))
        b.add_column(sa.Column("rank_key", sa.String(length=80), nullable=False, server_default=""))
        b.add_column(sa.Column("answer_version", sa.Integer(), nullable=True))
        b.add_column(sa.Column("answer_text", sa.String(length=500), nullable=True))
        b.add_column(sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("skipped_until", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("question", schema=None) as b:
        for c in (
            "skipped_until",
            "answered_at",
            "answer_text",
            "answer_version",
            "rank_key",
            "subject",
            "kind",
        ):
            b.drop_column(c)
    with op.batch_alter_table("proposal", schema=None) as b:
        for c in ("undone_at", "applied_version", "decided_by"):
            b.drop_column(c)
