# SPDX-License-Identifier: Apache-2.0
"""spec: the compiled, immutable artefact (append-only, like event)

Revision ID: 0010_spec
Revises: 0009_ontology_versions
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_spec"
down_revision = "0009_ontology_versions"
branch_labels = None
depends_on = None

MSG = "spec is append-only"


def upgrade() -> None:
    op.create_table(
        "spec",
        sa.Column("ontology_version", sa.Integer(), nullable=False),
        sa.Column("compiler_version", sa.String(length=32), nullable=False),
        sa.Column("compiled", sa.JSON(), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_spec_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spec")),
        sa.UniqueConstraint(
            "company_id", "ontology_version", name=op.f("uq_spec_company_id_ontology_version")
        ),
    )

    with op.batch_alter_table("spec", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_spec_company_id"), ["company_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_spec_hash"), ["hash"], unique=False)

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION othor_spec_append_only() RETURNS trigger AS $$ "
            f"BEGIN RAISE EXCEPTION '{MSG}'; END; $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER spec_append_only BEFORE UPDATE OR DELETE ON spec "
            "FOR EACH ROW EXECUTE FUNCTION othor_spec_append_only()"
        )
    elif dialect == "sqlite":
        for verb in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER spec_no_{verb.lower()} BEFORE {verb} ON spec "
                f"BEGIN SELECT RAISE(ABORT, '{MSG}'); END"
            )
    else:
        raise RuntimeError(f"no append-only trigger for dialect {dialect}")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS spec_append_only ON spec")
        op.execute("DROP FUNCTION IF EXISTS othor_spec_append_only()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS spec_no_update")
        op.execute("DROP TRIGGER IF EXISTS spec_no_delete")
    with op.batch_alter_table("spec", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_spec_hash"))
        batch_op.drop_index(batch_op.f("ix_spec_company_id"))

    op.drop_table("spec")
