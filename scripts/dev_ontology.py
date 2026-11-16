# SPDX-License-Identifier: Apache-2.0
"""Dev helper: apply the sample Ontology (boards 6a, 6a1) to a company on `make dev`, on top of
the table uses its scans wrote. The real flow drafts metrics in M4; this stands in until then.

    uv run python scripts/dev_ontology.py                 # indigothread
    docker compose exec app othor compile indigothread        # then: othor export-spec indigothread
"""

from __future__ import annotations

import argparse
import os

import sqlalchemy as sa

from othor.core.db import make_engine, make_session_factory
from othor.core.models import Company
from othor.core.tenancy import system_scope, tenant_scope
from othor.ontology import service
from tests.fixtures.sample_company.ontology import sample_ontology


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="indigothread")
    ap.add_argument(
        "--db",
        default=os.environ.get(
            "OTHOR_DEV_DB_URL", "postgresql+psycopg://othor:othor@localhost:5442/othor"
        ),
    )
    a = ap.parse_args()
    sf = make_session_factory(make_engine(a.db))
    with sf() as s, system_scope("dev: sample ontology"):
        c = s.scalars(sa.select(Company).where(Company.slug == a.slug)).one()
    with sf() as s, tenant_scope(c.id):
        v = service.apply(
            s, sample_ontology(), actor="dev", reason="Sample Ontology (boards 6a, 6a1)"
        )
        s.commit()
        print(f"Ontology v{v.number}" if v else "already applied (no change)")


if __name__ == "__main__":
    main()
