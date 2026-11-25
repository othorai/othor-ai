# SPDX-License-Identifier: Apache-2.0
"""company.tab_icon: the browser tab's icon (Company identity 1.0, owner 24 Nov 2026). Empty: the
logo when there is one, else the letters of the name; "letters": the letters even with a logo.

Revision ID: 0047_company_tab_icon
Revises: 0046_model_setting
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0047_company_tab_icon"
down_revision = "0046_model_setting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # plain ADD COLUMN, never a table rebuild (as 0044: a rebuild of `company` on SQLite breaks
    # the foreign keys that point at it)
    op.add_column("company", sa.Column("tab_icon", sa.String(length=8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("company", recreate="never") as t:
        t.drop_column("tab_icon")
