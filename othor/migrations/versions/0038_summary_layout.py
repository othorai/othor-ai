# SPDX-License-Identifier: Apache-2.0
"""summary_layout: the owner's Summary page, one row per company (v0.16, M16.2).

Revision ID: 0038_summary_layout
Revises: 0037_model_call_created
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_summary_layout"
down_revision = "0037_model_call_created"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "summary_layout",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("order", sa.JSON(), nullable=True),
        sa.Column("hidden", sa.JSON(), nullable=True),
        sa.Column("leads", sa.JSON(), nullable=True),
        sa.Column("view", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_summary_layout_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_summary_layout")),
        sa.UniqueConstraint("company_id", name=op.f("uq_summary_layout_company_id")),
    )
    op.create_index(
        op.f("ix_summary_layout_company_id"), "summary_layout", ["company_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_summary_layout_company_id"), table_name="summary_layout")
    op.drop_table("summary_layout")
