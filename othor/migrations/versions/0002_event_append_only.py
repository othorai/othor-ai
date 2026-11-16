# SPDX-License-Identifier: Apache-2.0
"""event table, append-only (ORM guard + database trigger)

The trigger is a second line of defence behind the ORM guard in othor.core.tenancy, so the
core schema stays portable: Postgres uses plpgsql, SQLite uses RAISE(ABORT).

Revision ID: 0002_event_append_only
Revises: 0001_core_tenancy
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_event_append_only"
down_revision = "0001_core_tenancy"
branch_labels = None
depends_on = None

MSG = "event is append-only"


def upgrade() -> None:
    op.create_table(
        "event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("object_type", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(100), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], name="fk_event_company_id_company"),
        sa.PrimaryKeyConstraint("id", name="pk_event"),
    )
    op.create_index("ix_event_company_id", "event", ["company_id"])
    op.create_index("ix_event_company_at", "event", ["company_id", "at"])
    op.create_index("ix_event_company_object", "event", ["company_id", "object_type", "object_id"])

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION othor_event_append_only() RETURNS trigger AS $$ "
            f"BEGIN RAISE EXCEPTION '{MSG}'; END; $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER event_append_only BEFORE UPDATE OR DELETE ON event "
            "FOR EACH ROW EXECUTE FUNCTION othor_event_append_only()"
        )
        op.execute(
            "CREATE TRIGGER event_no_truncate BEFORE TRUNCATE ON event "
            "FOR EACH STATEMENT EXECUTE FUNCTION othor_event_append_only()"
        )
    elif dialect == "sqlite":
        for verb in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER event_no_{verb.lower()} BEFORE {verb} ON event "
                f"BEGIN SELECT RAISE(ABORT, '{MSG}'); END"
            )
    else:
        raise RuntimeError(f"no append-only trigger for dialect {dialect}")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS event_no_truncate ON event")
        op.execute("DROP TRIGGER IF EXISTS event_append_only ON event")
        op.execute("DROP FUNCTION IF EXISTS othor_event_append_only()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS event_no_update")
        op.execute("DROP TRIGGER IF EXISTS event_no_delete")
    op.drop_index("ix_event_company_object", table_name="event")
    op.drop_index("ix_event_company_at", table_name="event")
    op.drop_index("ix_event_company_id", table_name="event")
    op.drop_table("event")
