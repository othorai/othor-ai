# SPDX-License-Identifier: Apache-2.0
"""access_request: how many people work at the company, and what it runs today (owner, 10 Nov
2026, the new Request access form). Nullable: requests made before keep their rows as they were.

Revision ID: 0032_access_request_more
Revises: 0031_invitations
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_access_request_more"
down_revision = "0031_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("access_request") as t:
        t.add_column(sa.Column("team_size", sa.String(length=32), nullable=True))
        t.add_column(sa.Column("runs_today", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("access_request") as t:
        t.drop_column("runs_today")
        t.drop_column("team_size")
