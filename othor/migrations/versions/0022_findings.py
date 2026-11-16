# SPDX-License-Identifier: Apache-2.0
"""findings, detector results and watches (M6a)

Revision ID: 0022_findings
Revises: 0021_job_dedupe
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_findings"
down_revision = "0021_job_dedupe"
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
        "finding",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("detector", sa.String(length=40), nullable=False),
        sa.Column("metric", sa.String(length=100), nullable=False),
        sa.Column("breakdown", sa.String(length=64), nullable=False),
        sa.Column("breakdown_value", sa.String(length=200), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.String(length=8), nullable=True),
        sa.Column("money", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("change", sa.String(length=24), nullable=False),
        sa.Column("first_seen_run", sa.Integer(), nullable=False),
        sa.Column("days_open", sa.Integer(), nullable=False),
        sa.Column("series_version", sa.String(length=16), nullable=True),
        sa.Column("body", sa.JSON(), nullable=False),
        *_tenant("finding"),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_finding_run_id_run")),
        sa.UniqueConstraint("run_id", "fingerprint", name=op.f("uq_finding_run_id_fingerprint")),
    )
    with op.batch_alter_table("finding", schema=None) as b:
        b.create_index(b.f("ix_finding_company_id"), ["company_id"], unique=False)
        b.create_index(b.f("ix_finding_run_id"), ["run_id"], unique=False)
        b.create_index(b.f("ix_finding_fingerprint"), ["fingerprint"], unique=False)
    op.create_table(
        "detector_result",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("detector", sa.String(length=40), nullable=False),
        sa.Column("version", sa.String(length=8), nullable=False),
        sa.Column("series", sa.JSON(), nullable=False),
        sa.Column("statistic", sa.String(length=40), nullable=True),
        sa.Column("p_value", sa.String(length=40), nullable=True),
        sa.Column("q_value", sa.String(length=40), nullable=True),
        sa.Column("effect", sa.String(length=40), nullable=True),
        sa.Column("money", sa.String(length=32), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("reason_if_not", sa.String(length=200), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False),
        *_tenant("detector_result"),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_detector_result_run_id_run")),
    )
    with op.batch_alter_table("detector_result", schema=None) as b:
        b.create_index(b.f("ix_detector_result_company_id"), ["company_id"], unique=False)
        b.create_index(b.f("ix_detector_result_run_id"), ["run_id"], unique=False)
    op.create_table(
        "watch",
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("indicator", sa.JSON(), nullable=False),
        sa.Column("threshold", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("owner_name", sa.String(length=120), nullable=False),
        sa.Column("created_from_finding", sa.String(length=32), nullable=True),
        sa.Column("since", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("breached_since", sa.Date(), nullable=True),
        sa.Column("last_value", sa.String(length=32), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant("watch"),
    )
    with op.batch_alter_table("watch", schema=None) as b:
        b.create_index(b.f("ix_watch_company_id"), ["company_id"], unique=False)


def downgrade() -> None:
    for t in ("watch", "detector_result", "finding"):
        op.drop_table(t)
