# SPDX-License-Identifier: Apache-2.0
"""company.logo_sha: the company's logo, re-encoded and stored by its hash (M15.7).

Revision ID: 0036_company_logo
Revises: 0035_connector_request
Create Date: 2026-11-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0036_company_logo"
down_revision = "0035_connector_request"
branch_labels = None
depends_on = None


# recreate="never": SQLite (3.35+) adds and drops the column in place; rebuilding company
# would break the foreign keys that point at it.


def upgrade() -> None:
    with op.batch_alter_table("company", recreate="never") as b:
        b.add_column(sa.Column("logo_sha", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("company", recreate="never") as b:
        b.drop_column("logo_sha")
