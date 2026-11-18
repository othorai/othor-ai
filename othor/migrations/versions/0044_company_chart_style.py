# SPDX-License-Identifier: Apache-2.0
"""company.chart_smooth and company.chart_values: Settings › Charts (v0.25 M25.1, owner 18 Nov 2026):
smooth lines on by default; values on lines, Key points by default.

Revision ID: 0044_company_chart_style
Revises: 0043_contact_topic
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_company_chart_style"
down_revision = "0043_contact_topic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # plain ADD COLUMN, never a table rebuild: on SQLite a rebuild of `company` breaks the
    # foreign keys that point at it
    op.add_column(
        "company",
        sa.Column("chart_smooth", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "company",
        sa.Column("chart_values", sa.String(length=8), nullable=False, server_default="key"),
    )


def downgrade() -> None:
    with op.batch_alter_table("company", recreate="never") as t:  # as 0042: no table rebuild
        t.drop_column("chart_values")
        t.drop_column("chart_smooth")
