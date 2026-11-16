# SPDX-License-Identifier: Apache-2.0
"""core tenancy: company, app_user, membership

Revision ID: 0001_core_tenancy
Revises:
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_core_tenancy"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("website", sa.String(300), nullable=True),
        sa.Column("fiscal_year_start_month", sa.SmallInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("number_format", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("run_time", sa.String(5), nullable=False),
        sa.Column("brief_time", sa.String(5), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_company"),
        sa.UniqueConstraint("slug", name="uq_company_slug"),
    )
    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_app_user"),
        sa.UniqueConstraint("email", name="uq_app_user_email"),
    )
    op.create_table(
        "membership",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name="fk_membership_company_id_company"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_membership_user_id_app_user"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_membership"),
        sa.UniqueConstraint("company_id", "user_id", name="uq_membership_company_id_user_id"),
    )
    op.create_index("ix_membership_company_id", "membership", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_membership_company_id", table_name="membership")
    op.drop_table("membership")
    op.drop_table("app_user")
    op.drop_table("company")
