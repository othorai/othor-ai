# SPDX-License-Identifier: Apache-2.0
"""`make demo`: the Indigo Thread sample company set up in a running app, as an owner would leave it
after onboarding: the company, its owner, the two sample databases connected read-only, the hand-built
Business Model confirmed (with the Surat shutdown answered), setup done. `othor demo` then runs one
night. Runs inside the app container (it holds the instance key and the data folder), after
`make dev && make seed`. Dev and demo only: not in the image, like `othor seed`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import sqlalchemy as sa

from othor.connectors import credentials
from othor.connectors.models import Source
from othor.core.events import record_event
from othor.core.models import AppUser, Company, Membership, SetupState
from othor.core.tenancy import system_scope, tenant_scope
from othor.engine.context import EngineContext
from othor.ontology import service
from tests.fixtures.sample_company import generate
from tests.fixtures.sample_company.catalog import catalog_from_generator
from tests.fixtures.sample_company.ontology import sample_ontology, surat_answer

SLUG = "indigothread"
NAME = "Indigo Thread Co."
EMAIL = "owner@indigothread.example"
OWNER_NAME = "Uday Kamath"

# the sample databases as the app and worker containers reach them (docker-compose.yml)
CONFIG = {
    "erp_db": {"host": "erp-db", "port": 3306, "database": "erp_db", "user": "othor_ro"},
    "sales_dw": {
        "host": "sales-dw",
        "port": 5432,
        "database": "sales_dw",
        "user": "othor_ro",
        "schemas": ["public", "sales_dw"],
    },
}
PASSWORD = "othor_ro_dev"  # the read-only login `make seed` creates in both sample databases

# the public demo in US dollars by default, rupees on request (owner, 25 Nov 2026); the time zone
# follows, as in the README's pictures. Number grouping follows the currency (othor/core/fmt.py).
TIMEZONE = {"USD": "America/New_York", "INR": "Asia/Kolkata"}


@dataclass(frozen=True)
class Installed:
    company_id: uuid.UUID
    created: bool
    user_id: uuid.UUID | None = None  # the owner, for the one-time sign-in link
    currency: str = "USD"


def install(
    ctx: EngineContext, *, as_of: dt.date = dt.date(2026, 11, 4), currency: str = "USD"
) -> Installed:
    """Idempotent: an existing indigothread is left as it is (created=False), its currency too."""
    if currency not in TIMEZONE:
        raise ValueError(f"currency is one of {', '.join(sorted(TIMEZONE))}")
    with ctx.sf() as s, system_scope("demo: install the sample company"):
        found = s.scalars(sa.select(Company).where(Company.slug == SLUG)).first()
        user = s.scalars(sa.select(AppUser).where(AppUser.email == EMAIL)).first()
        if found is not None:
            return Installed(
                found.id, created=False, user_id=user.id if user else None, currency=found.currency
            )
        if user is None:
            user = AppUser(email=EMAIL, name=OWNER_NAME)
            s.add(user)
        company = Company(
            slug=SLUG,
            name=NAME,
            website="https://indigothread.example",
            fiscal_year_start_month=4,
            currency=currency,
            timezone=TIMEZONE[currency],
        )
        s.add(company)
        s.flush()
        company.workspace_id = company.id
        cid, uid = company.id, user.id
        s.commit()

    sample = generate.generate(as_of=as_of)  # the same data `make seed` loaded
    with ctx.sf() as s, tenant_scope(cid):
        s.add(Membership(company_id=cid, user_id=uid, role="owner"))
        ids = catalog_from_generator(s, sample)
        for name, sid in ids.items():
            src = s.scalars(sa.select(Source).where(Source.id == sid)).one()
            src.config = {**CONFIG[name], "ssl": "disable"}
            credentials.store(s, ctx.key, cid, sid, {"password": PASSWORD})
        service.apply(s, sample_ontology(), actor=str(uid), reason="Sample Business Model")
        service.confirm(s, actor=str(uid))
        # the owner's Home answer of 28 Oct: the Surat shutdown is the same dates every year
        service.apply(s, surat_answer(), actor=str(uid), reason="Answered on Home")
        s.add(SetupState(company_id=cid, current_step="done", step_data={}))
        record_event(
            s,
            actor="demo",
            action="demo.installed",
            object_type="company",
            object_id=cid,
            after={
                "slug": SLUG,
                "sources": sorted(ids),
                "as_of": as_of.isoformat(),
                "currency": currency,
            },
        )
        s.commit()
    return Installed(cid, created=True, user_id=uid, currency=currency)
