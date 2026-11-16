# SPDX-License-Identifier: Apache-2.0
"""what an uploaded document changed in the setup drafts (M4b: 2ne, 2nf)

Revision ID: 0018_setup_changes
Revises: 0017_upload_deleted
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_setup_changes"
down_revision = "0017_upload_deleted"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setup_change",
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("draft_round", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=120), nullable=False),
        sa.Column("draft_key", sa.String(length=100), nullable=True),
        sa.Column("quote", sa.String(length=500), nullable=True),
        sa.Column("locator", sa.String(length=80), nullable=True),
        sa.Column("result", sa.String(length=24), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_setup_change_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_setup_change")),
    )
    with op.batch_alter_table("setup_change", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_setup_change_company_id"), ["company_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("setup_change", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_setup_change_company_id"))
    op.drop_table("setup_change")
