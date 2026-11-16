# SPDX-License-Identifier: Apache-2.0
"""Ontology versions; existing records (M2 table uses) become version 1 per company

Revision ID: 0009_ontology_versions
Revises: 0008_ontology_records_ai_log
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_ontology_versions"
down_revision = "0008_ontology_records_ai_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_version",
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("source_ref", sa.JSON(), nullable=True),
        sa.Column("diff", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_ontology_version_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_version")),
        sa.UniqueConstraint(
            "company_id", "number", name=op.f("uq_ontology_version_company_id_number")
        ),
    )

    with op.batch_alter_table("ontology_version", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ontology_version_company_id"), ["company_id"], unique=False
        )

    # Backfill: every company with records gets v1; its records are live from v1.
    import uuid
    from datetime import UTC, datetime

    bind = op.get_bind()
    now = datetime.now(UTC)
    companies = bind.execute(sa.text("SELECT DISTINCT company_id FROM ontology_record")).fetchall()
    version = sa.table(
        "ontology_version",
        sa.column("id", sa.Uuid()),
        sa.column("company_id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("number", sa.Integer()),
        sa.column("created_by", sa.String()),
        sa.column("reason", sa.String()),
        sa.column("source_ref", sa.JSON()),
        sa.column("diff", sa.JSON()),
    )
    for (cid,) in companies:
        cid = cid if isinstance(cid, uuid.UUID) else uuid.UUID(str(cid))
        op.bulk_insert(
            version,
            [
                {
                    "id": uuid.uuid4(),
                    "company_id": cid,
                    "workspace_id": cid,
                    "created_at": now,
                    "number": 1,
                    "created_by": "system",
                    "reason": "Table uses from the scans (M2)",
                    "source_ref": {"migration": "0009"},
                    "diff": {"backfill": True},
                }
            ],
        )
    bind.execute(sa.text("UPDATE ontology_record SET version_from = 1 WHERE version_from IS NULL"))
    bind.execute(
        sa.text(
            "UPDATE ontology_record SET version_to = 1 WHERE status = 'retired' AND version_to IS NULL"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE ontology_record SET version_from = NULL, version_to = NULL")
    )
    with op.batch_alter_table("ontology_version", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ontology_version_company_id"))

    op.drop_table("ontology_version")
