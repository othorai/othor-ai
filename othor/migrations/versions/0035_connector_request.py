# SPDX-License-Identifier: Apache-2.0
"""connector_request: a connector the owner asked for from Add a source (M15.4).

Revision ID: 0035_connector_request
Revises: 0034_question_draft
Create Date: 2026-11-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_connector_request"
down_revision = "0034_question_draft"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_request",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("system", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=60), nullable=True),
        sa.Column("wants", sa.Text(), nullable=True),
        sa.Column("reach", sa.String(length=16), nullable=True),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_connector_request_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_request")),
    )
    op.create_index(
        op.f("ix_connector_request_company_id"), "connector_request", ["company_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_connector_request_company_id"), table_name="connector_request")
    op.drop_table("connector_request")
