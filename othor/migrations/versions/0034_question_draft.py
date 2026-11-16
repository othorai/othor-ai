# SPDX-License-Identifier: Apache-2.0
"""question.draft: a typed answer drafted by the model waits here until the owner applies it
(non-negotiable 6; it was written to the Ontology at once before, 12 Nov 2026).

Revision ID: 0034_question_draft
Revises: 0033_worker_heartbeat
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_question_draft"
down_revision = "0033_worker_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("question", schema=None) as b:
        b.add_column(sa.Column("draft", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("question", schema=None) as b:
        b.drop_column("draft")
