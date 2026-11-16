# SPDX-License-Identifier: Apache-2.0
"""auth: login_code, auth_session

Revision ID: 0003_auth
Revises: 0002_event_append_only
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_auth"
down_revision = "0002_event_append_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_code",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("resend_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name="fk_login_code_company_id_company"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_login_code"),
    )
    op.create_index("ix_login_code_lookup", "login_code", ["email", "purpose", "company_id"])
    op.create_table(
        "auth_session",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("handoff_hash", sa.String(64), nullable=True),
        sa.Column("handoff_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name="fk_auth_session_company_id_company"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_auth_session_user_id_app_user"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_session"),
        sa.UniqueConstraint("token_hash", name="uq_auth_session_token_hash"),
        sa.UniqueConstraint("handoff_hash", name="uq_auth_session_handoff_hash"),
    )
    op.create_index("ix_auth_session_company_id", "auth_session", ["company_id"])
    op.create_index("ix_auth_session_user_id", "auth_session", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_session_user_id", table_name="auth_session")
    op.drop_index("ix_auth_session_company_id", table_name="auth_session")
    op.drop_table("auth_session")
    op.drop_index("ix_login_code_lookup", table_name="login_code")
    op.drop_table("login_code")
