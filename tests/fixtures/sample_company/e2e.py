# SPDX-License-Identifier: Apache-2.0
"""The real-path nights (M6a, decided 5 Nov 2026): the sample company's sources loaded *as of each
night* from the generator, into their own databases (`erp_e2e` in the sample MySQL,
`sales_dw_e2e` in the sample Postgres), so the shared sample databases are never touched.

The Ontology and the catalog are the sample's, pointed at those databases: in MySQL a database
is the schema, so every table reference to `erp_db` becomes `erp_e2e` (the source keeps its name,
erp_db). Postgres keeps the `sales_dw` schema inside the other database.
"""

from __future__ import annotations

import copy
import datetime as dt
import os
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from othor.ontology.service import Change
from tests.fixtures.sample_company.generate import SampleCompany, generate
from tests.fixtures.sample_company.load import load_mysql, load_postgres, write_sheets

STREAM = os.environ.get("OTHOR_SAMPLE_STREAM", "shared")
DEMO = STREAM == "demo"
# one pair per pytest-xdist worker (gw0, gw1, …): the nights load them, so workers can't share them
_W = f"_{w}" if (w := os.environ.get("PYTEST_XDIST_WORKER")) else ""
ERP_DB = ("erp_e2e_demo" if DEMO else "erp_e2e") + _W
DW_DB = ("sales_dw_e2e_demo" if DEMO else "sales_dw_e2e") + _W
RO = "othor_ro"

# the tables the sample Ontology reads (the generator's fillers aren't loaded: faster nights)
ERP_TABLES = (
    "approvals",
    "bank_ledger",
    "cost_ledger",
    "credit_notes",
    "customers",
    "fx_rates",
    "invoice_lines",
    "invoices",
    "items",
    "receivables",
)


def config(name: str) -> dict[str, Any]:
    from tests.integration.setup_world import CONFIG

    return {**CONFIG[name], "database": {"erp_db": ERP_DB, "sales_dw": DW_DB}[name]}


def _url(admin: str, database: str) -> str:
    return sa.make_url(admin).set(database=database).render_as_string(hide_password=False)


def ensure_databases(erp_admin: str, dw_admin: str) -> tuple[str, str]:
    """Create the two e2e databases (once) with the read-only grants a customer would give."""
    eng = sa.create_engine(erp_admin)
    with eng.begin() as c:
        c.execute(sa.text(f"CREATE DATABASE IF NOT EXISTS `{ERP_DB}`"))
        c.execute(sa.text(f"GRANT SELECT, SHOW VIEW ON `{ERP_DB}`.* TO '{RO}'@'%'"))
    eng.dispose()
    eng = sa.create_engine(dw_admin, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        if not c.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": DW_DB}
        ).first():
            c.execute(sa.text(f'CREATE DATABASE "{DW_DB}"'))
        c.execute(sa.text(f'GRANT CONNECT ON DATABASE "{DW_DB}" TO {RO}'))
    eng.dispose()
    return _url(erp_admin, ERP_DB), _url(dw_admin, DW_DB)


def as_of(night: dt.date) -> SampleCompany:
    """What the sources hold when the run of `night` reads them (everything before that day)."""
    if DEMO:
        from tests.fixtures.sample_company.demo_stream import generate_demo

        return generate_demo(as_of=night)
    return generate(as_of=night)


def load_night(c: SampleCompany, erp_url: str, dw_url: str, sheets_dir: Path) -> None:
    load_mysql(erp_url, {k: c.erp[k] for k in ERP_TABLES})
    load_postgres(dw_url, c.dw)
    write_sheets(c, sheets_dir)


def retarget(changes: list[Change]) -> list[Change]:
    """The sample Ontology read from the e2e databases: `erp_db` table references → `erp_e2e`."""

    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            out = {k: walk(x) for k, x in v.items()}
            if out.get("source") == "erp_db" and out.get("schema") == "erp_db":
                out["schema"] = ERP_DB
            return out
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return [
        Change(ch.op, ch.type, ch.key, walk(copy.deepcopy(ch.body)), ch.source_ref)
        for ch in changes
    ]
