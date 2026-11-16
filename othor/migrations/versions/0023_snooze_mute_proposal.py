# SPDX-License-Identifier: Apache-2.0
"""snoozes, mutes and proposals (M6b)

Revision ID: 0023_snooze_mute_proposal
Revises: 0022_findings
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_snooze_mute_proposal"
down_revision = "0022_findings"
branch_labels = None
depends_on = None


def _tenant(name: str) -> list:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f(f"fk_{name}_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
    ]


def upgrade() -> None:
    op.create_table(
        "snooze",
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("until", sa.Date(), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant("snooze"),
    )
    with op.batch_alter_table("snooze", schema=None) as b:
        b.create_index(b.f("ix_snooze_company_id"), ["company_id"], unique=False)
        b.create_index(b.f("ix_snooze_fingerprint"), ["fingerprint"], unique=False)
    op.create_table(
        "mute",
        sa.Column("pattern", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant("mute"),
    )
    with op.batch_alter_table("mute", schema=None) as b:
        b.create_index(b.f("ix_mute_company_id"), ["company_id"], unique=False)
        b.create_index(b.f("ix_mute_pattern"), ["pattern"], unique=False)
    op.create_table(
        "proposal",
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("origin", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant("proposal"),
        sa.UniqueConstraint(
            "company_id", "fingerprint", name=op.f("uq_proposal_company_id_fingerprint")
        ),
    )
    with op.batch_alter_table("proposal", schema=None) as b:
        b.create_index(b.f("ix_proposal_company_id"), ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_table("proposal")
    op.drop_table("mute")
    op.drop_table("snooze")
