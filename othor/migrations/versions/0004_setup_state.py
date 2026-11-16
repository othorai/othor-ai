# SPDX-License-Identifier: Apache-2.0
"""setup_state: the saved onboarding step

Revision ID: 0004_setup_state
Revises: 0003_auth
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_setup_state"
down_revision = "0003_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setup_state",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_step", sa.String(32), nullable=False),
        sa.Column("step_data", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name="fk_setup_state_company_id_company"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_setup_state"),
        sa.UniqueConstraint("company_id", name="uq_setup_state_company_id"),
    )
    op.create_index("ix_setup_state_company_id", "setup_state", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_setup_state_company_id", table_name="setup_state")
    op.drop_table("setup_state")
