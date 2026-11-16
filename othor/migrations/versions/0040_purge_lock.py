# SPDX-License-Identifier: Apache-2.0
"""purge_lock, company.purged_at: the purge after 30 days (M18.3).

The append-only triggers on `event` and `spec` let a DELETE through only for a company that has
a `purge_lock` row, and that row exists only inside the purge's own transaction. UPDATE and
TRUNCATE stay refused; `platform_event` is never purged and its triggers are not touched.

Revision ID: 0040_purge_lock
Revises: 0039_company_closed
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0040_purge_lock"
down_revision = "0039_company_closed"
branch_labels = None
depends_on = None

TABLES = ("event", "spec")
LOCKED = "EXISTS (SELECT 1 FROM purge_lock WHERE purge_lock.company_id = OLD.company_id)"


def _pg_function(table: str, *, purge: bool) -> str:
    msg = f"{table} is append-only"
    body = (
        f"BEGIN IF TG_OP = 'DELETE' AND {LOCKED} THEN RETURN OLD; END IF; "
        f"RAISE EXCEPTION '{msg}'; END;"
        if purge
        else f"BEGIN RAISE EXCEPTION '{msg}'; END;"
    )
    return (
        f"CREATE OR REPLACE FUNCTION othor_{table}_append_only() RETURNS trigger AS $$ "
        f"{body} $$ LANGUAGE plpgsql"
    )


def _sqlite_delete_trigger(table: str, *, purge: bool) -> str:
    when = f" WHEN NOT {LOCKED}" if purge else ""
    return (
        f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}{when} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
    )


def upgrade() -> None:
    op.create_table(
        "purge_lock",
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name="fk_purge_lock_company_id_company"
        ),
        sa.PrimaryKeyConstraint("company_id", name="pk_purge_lock"),
    )
    # recreate="never", as in 0036 and 0039: rebuilding company would break its foreign keys
    with op.batch_alter_table("company", recreate="never") as b:
        b.add_column(sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
    _triggers(purge=True)


def downgrade() -> None:
    _triggers(purge=False)
    with op.batch_alter_table("company", recreate="never") as b:
        b.drop_column("purged_at")
    op.drop_table("purge_lock")


def _triggers(*, purge: bool) -> None:
    dialect = op.get_bind().dialect.name
    for table in TABLES:
        if dialect == "postgresql":
            op.execute(_pg_function(table, purge=purge))
        elif dialect == "sqlite":
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")
            op.execute(_sqlite_delete_trigger(table, purge=purge))
        else:
            raise RuntimeError(f"no append-only trigger for dialect {dialect}")
