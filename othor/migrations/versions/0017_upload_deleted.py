# SPDX-License-Identifier: Apache-2.0
"""uploads are soft-deleted (removed from 2c, kept for provenance)

Revision ID: 0017_upload_deleted
Revises: 0016_setup_drafts
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_upload_deleted"
down_revision = "0016_setup_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("upload") as b:
        b.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("upload") as b:
        b.drop_column("deleted_at")
