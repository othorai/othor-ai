# SPDX-License-Identifier: Apache-2.0
"""rate_limit: per-IP fixed windows for sign-in and signup (M11a). Instance-level, no
company_id (counted before a company is known); the IP is stored only as an HMAC.

Revision ID: 0030_rate_limit
Revises: 0029_content_hash
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_rate_limit"
down_revision = "0029_content_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bucket", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rate_limit"),
        sa.UniqueConstraint(
            "bucket", "key_hash", "window_start", name="uq_rate_limit_bucket_key_hash_window_start"
        ),
    )


def downgrade() -> None:
    op.drop_table("rate_limit")
