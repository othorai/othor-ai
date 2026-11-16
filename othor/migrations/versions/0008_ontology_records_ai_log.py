# SPDX-License-Identifier: Apache-2.0
"""Ontology records (table_use first) and the model call log

Revision ID: 0008_ontology_records_ai_log
Revises: 0007_jobs_query_log
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_ontology_records_ai_log"
down_revision = "0007_jobs_query_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_record",
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source_ref", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("version_from", sa.Integer(), nullable=True),
        sa.Column("version_to", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_ontology_record_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_record")),
    )

    with op.batch_alter_table("ontology_record", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ontology_record_company_id"), ["company_id"], unique=False
        )
        batch_op.create_index(
            "ix_ontology_record_key", ["company_id", "type", "key", "status"], unique=False
        )

    op.create_table(
        "model_call",
        sa.Column("prompt_id", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("guard_result", sa.String(length=32), nullable=False),
        sa.Column("cached", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_model_call_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_call")),
    )

    with op.batch_alter_table("model_call", schema=None) as batch_op:
        batch_op.create_index(
            "ix_model_call_cache", ["company_id", "prompt_id", "input_hash"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_model_call_company_id"), ["company_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("ontology_record", schema=None) as batch_op:
        batch_op.drop_index("ix_ontology_record_key")
        batch_op.drop_index(batch_op.f("ix_ontology_record_company_id"))

    op.drop_table("ontology_record")

    with op.batch_alter_table("model_call", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_model_call_company_id"))
        batch_op.drop_index("ix_model_call_cache")

    op.drop_table("model_call")
