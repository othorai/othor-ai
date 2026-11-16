# SPDX-License-Identifier: Apache-2.0
"""model_call (company_id, created_at): the AI usage page reads one company's month (M15.8).

Revision ID: 0037_model_call_created
Revises: 0036_company_logo
Create Date: 2026-11-14
"""

from alembic import op

revision = "0037_model_call_created"
down_revision = "0036_company_logo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_model_call_created", "model_call", ["company_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_model_call_created", table_name="model_call")
