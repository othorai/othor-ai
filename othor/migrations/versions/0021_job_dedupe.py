# SPDX-License-Identifier: Apache-2.0
"""job.dedupe_key (one nightly run per company and day); one spec per version and compiler

Revision ID: 0021_job_dedupe
Revises: 0020_series_meta
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_job_dedupe"
down_revision = "0020_series_meta"
branch_labels = None
depends_on = None

MSG = "spec is append-only"


def _sqlite_spec_triggers(create: bool) -> None:
    # SQLite's batch mode rebuilds the spec table, which drops its triggers: put them back
    for verb in ("UPDATE", "DELETE"):
        op.execute(f"DROP TRIGGER IF EXISTS spec_no_{verb.lower()}")
        if create:
            op.execute(
                f"CREATE TRIGGER spec_no_{verb.lower()} BEFORE {verb} ON spec "
                f"BEGIN SELECT RAISE(ABORT, '{MSG}'); END"
            )


def _spec_key(old: list[str], new: list[str], old_name: str, new_name: str) -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        _sqlite_spec_triggers(create=False)
    with op.batch_alter_table("spec", schema=None) as b:
        b.drop_constraint(old_name, type_="unique")
        b.create_unique_constraint(new_name, new)
    if sqlite:
        _sqlite_spec_triggers(create=True)


def upgrade() -> None:
    with op.batch_alter_table("job", schema=None) as b:
        b.add_column(sa.Column("dedupe_key", sa.String(length=80), nullable=True))
        b.create_unique_constraint(
            b.f("uq_job_company_id_dedupe_key"), ["company_id", "dedupe_key"]
        )
    _spec_key(
        ["company_id", "ontology_version"],
        ["company_id", "ontology_version", "compiler_version"],
        op.f("uq_spec_company_id_ontology_version"),
        op.f("uq_spec_company_id_ontology_version_compiler_version"),
    )


def downgrade() -> None:
    _spec_key(
        ["company_id", "ontology_version", "compiler_version"],
        ["company_id", "ontology_version"],
        op.f("uq_spec_company_id_ontology_version_compiler_version"),
        op.f("uq_spec_company_id_ontology_version"),
    )
    with op.batch_alter_table("job", schema=None) as b:
        b.drop_constraint(b.f("uq_job_company_id_dedupe_key"), type_="unique")
        b.drop_column("dedupe_key")
