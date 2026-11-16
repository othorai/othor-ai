# SPDX-License-Identifier: Apache-2.0
"""company.email_delivery (Settings · Delivery, 7a)

Revision ID: 0005_company_delivery
Revises: 0004_setup_state
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_company_delivery"
down_revision = "0004_setup_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("company") as b:
        b.add_column(
            sa.Column("email_delivery", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("company") as b:
        b.drop_column("email_delivery")
