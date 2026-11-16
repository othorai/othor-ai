# SPDX-License-Identifier: Apache-2.0
"""invitation, access_request, platform_event: sign-up by invitation and Request access (M11d
part C). Instance-level tables, no company_id (they exist before a company does). Only
othor.core.auth touches invitation and access_request; platform_event is append-only, enforced
by a trigger as `event` is (0002).

Revision ID: 0031_invitations
Revises: 0030_rate_limit
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_invitations"
down_revision = "0030_rate_limit"
branch_labels = None
depends_on = None

MSG = "platform_event is append-only"


def upgrade() -> None:
    op.create_table(
        "invitation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by_company", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["used_by_company"], ["company.id"], name="fk_invitation_used_by_company_company"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invitation"),
        sa.UniqueConstraint("code_hash", name="uq_invitation_code_hash"),
    )
    op.create_table(
        "access_request",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("company", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_access_request"),
        sa.UniqueConstraint("email", "day", name="uq_access_request_email_day"),
    )
    op.create_table(
        "platform_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.String(length=100), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_platform_event"),
    )
    op.create_index("ix_platform_event_at", "platform_event", ["at"])

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION othor_platform_event_append_only() RETURNS trigger AS $$ "
            f"BEGIN RAISE EXCEPTION '{MSG}'; END; $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER platform_event_append_only BEFORE UPDATE OR DELETE ON platform_event "
            "FOR EACH ROW EXECUTE FUNCTION othor_platform_event_append_only()"
        )
        op.execute(
            "CREATE TRIGGER platform_event_no_truncate BEFORE TRUNCATE ON platform_event "
            "FOR EACH STATEMENT EXECUTE FUNCTION othor_platform_event_append_only()"
        )
    elif dialect == "sqlite":
        for verb in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER platform_event_no_{verb.lower()} BEFORE {verb} ON platform_event "
                f"BEGIN SELECT RAISE(ABORT, '{MSG}'); END"
            )
    else:
        raise RuntimeError(f"no append-only trigger for dialect {dialect}")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS platform_event_no_truncate ON platform_event")
        op.execute("DROP TRIGGER IF EXISTS platform_event_append_only ON platform_event")
        op.execute("DROP FUNCTION IF EXISTS othor_platform_event_append_only()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS platform_event_no_update")
        op.execute("DROP TRIGGER IF EXISTS platform_event_no_delete")
    op.drop_index("ix_platform_event_at", table_name="platform_event")
    op.drop_table("platform_event")
    op.drop_table("access_request")
    op.drop_table("invitation")
