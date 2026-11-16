# SPDX-License-Identifier: Apache-2.0
"""company.purge_after, company.closed_by: the owner closed the company (M18.2).

Revision ID: 0039_company_closed
Revises: 0038_summary_layout
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_company_closed"
down_revision = "0038_summary_layout"
branch_labels = None
depends_on = None


# recreate="never", as in 0036: SQLite (3.35+) adds and drops the columns in place; rebuilding
# company would break the foreign keys that point at it.


def upgrade() -> None:
    with op.batch_alter_table("company", recreate="never") as b:
        b.add_column(sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("closed_by", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("company", recreate="never") as b:
        b.drop_column("closed_by")
        b.drop_column("purge_after")
