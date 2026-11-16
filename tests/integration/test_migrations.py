# SPDX-License-Identifier: Apache-2.0
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from othor.all_models import Base
from othor.core.db import make_engine
from othor.migrations.runner import downgrade, upgrade


def test_up_down_up(db_url: str) -> None:
    downgrade(db_url, "base")
    eng = make_engine(db_url)
    assert set(sa.inspect(eng).get_table_names()) <= {"alembic_version"}
    eng.dispose()
    upgrade(db_url)
    eng = make_engine(db_url)
    assert {"company", "app_user", "membership", "event"} <= set(sa.inspect(eng).get_table_names())
    eng.dispose()


def test_models_match_migrations(db_url: str) -> None:
    eng = make_engine(db_url)
    with eng.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
    eng.dispose()
    assert diff == [], diff
