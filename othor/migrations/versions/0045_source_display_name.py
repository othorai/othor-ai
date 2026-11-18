# SPDX-License-Identifier: Apache-2.0
"""source.display_name: a source's plain name ("Sales records") shown before its connector name
(0.26 M26.3, owner 18 Nov 2026).

Revision ID: 0045_source_display_name
Revises: 0044_company_chart_style
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0045_source_display_name"
down_revision = "0044_company_chart_style"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source", sa.Column("display_name", sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source", recreate="never") as t:
        t.drop_column("display_name")
