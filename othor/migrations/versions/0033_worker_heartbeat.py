# SPDX-License-Identifier: Apache-2.0
"""worker_heartbeat: one row, when the worker last ticked (M11b §6, after #16). Instance-level,
no company data, so no company_id.

Revision ID: 0033_worker_heartbeat
Revises: 0032_access_request_more
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_worker_heartbeat"
down_revision = "0032_access_request_more"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeat",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_worker_heartbeat"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeat")
