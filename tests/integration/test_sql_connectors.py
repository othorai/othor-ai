# SPDX-License-Identifier: Apache-2.0
"""Test connection and scan against the compose sample sources (needs `make dev && make seed`).

Acceptance (docs/10 M2): the scan of sales_dw sorts `employee` as Excluded and never selects its
personal columns (asserted from the query log); a writer role is refused.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from othor.connectors.sql.engine import SqlCreds, make_engine
from othor.connectors.sql.grants import REFUSED
from othor.connectors.sql.query_log import QueryLog, identifiers
from othor.connectors.sql.scan import TableScan, check_connection, scan, schemas_to_read
from tests.conftest import require_services
from tests.fixtures.sample_company import extras

TODAY = dt.date(2026, 11, 4)


def pg(user: str, schemas=("public", "sales_dw")) -> SqlCreds:
    return SqlCreds(
        "postgresql", "localhost", 5443, "sales_dw", user, f"{user}_dev", "disable", schemas
    )


def my(user: str) -> SqlCreds:
    return SqlCreds("mysql", "localhost", 3307, "erp_db", user, f"{user}_dev", "disable")


@pytest.fixture(scope="module", autouse=True)
def services(sample_sources_seeded):
    for c in (pg("othor_ro"), my("othor_ro")):
        r = check_connection(c)
        if not r.ok and r.message.startswith("Can't reach"):
            require_services("sample sources erp_db (localhost:3307) and sales_dw (localhost:5443)")


def test_read_only_users_pass():
    r = check_connection(pg("othor_ro"))
    assert r.ok, r
    assert r.tables == 48 and r.message == "Connected · read-only · 48 tables"
    assert r.latency_ms is not None
    r = check_connection(my("othor_ro"))
    assert r.ok, r
    assert r.tables == 64


def test_schemas_are_read_from_the_role_when_none_are_named():
    """M11c: no schema field. The scan reads every schema the read-only role can see, leaving
    out Postgres's own (pg_catalog, information_schema)."""
    c = pg("othor_ro", schemas=())
    eng = make_engine(c)
    try:
        with eng.connect() as conn:
            assert schemas_to_read(conn, c) == ("public", "sales_dw")
            assert schemas_to_read(conn, pg("othor_ro", schemas=("public",))) == ("public",)
    finally:
        eng.dispose()
    r = check_connection(c)
    assert r.ok and r.tables == 48
    found = {t.schema for t in scan(c, log=QueryLog(), today=TODAY)}
    assert found == {"public", "sales_dw"}


def test_writer_roles_are_refused():
    for c in (pg("othor_rw"), my("othor_rw")):
        r = check_connection(c)
        assert not r.ok
        assert r.message == REFUSED
        assert r.refused


def test_wrong_password_and_unreachable_host():
    c = pg("othor_ro")
    bad = SqlCreds(c.dialect, c.host, c.port, c.database, c.user, "nope", "disable", c.schemas)
    assert check_connection(bad).message == "Wrong user or password."
    gone = SqlCreds(c.dialect, "localhost", 1, c.database, c.user, "x", "disable", c.schemas)
    assert check_connection(gone).message.startswith("Can't reach localhost:1.")


@pytest.fixture(scope="module")
def dw_scan() -> tuple[dict[str, TableScan], QueryLog]:
    log = QueryLog()
    out = {f"{t.schema}.{t.name}": t for t in scan(pg("othor_ro"), log=log, today=TODAY)}
    return out, log


def test_sales_dw_scan_sorts_tables(dw_scan):
    tables, _ = dw_scan
    assert len(tables) == 48
    use = {k: (t.sorted.use, t.sorted.reason) for k, t in tables.items()}
    assert use["public.employee"] == ("excluded", None)
    assert use["sales_dw.crm_contacts"] == ("excluded", None)
    assert use["sales_dw.invoice_contacts"] == ("excluded", None)
    assert use["public.stg_orders"] == ("ignored", "staging")
    assert use["public.etl_log"] == ("ignored", "log")
    assert use["sales_dw.tmp_load"] == ("ignored", "empty")
    assert use["sales_dw.fact_invoice_2019"] == ("ignored", "old")
    assert use["sales_dw.fact_invoice"] == ("metric_source", None)
    assert use["sales_dw.dim_customer"] == ("dimension", None)
    emp = tables["public.employee"]
    assert emp.row_count == 2140
    assert {c.name for c in emp.columns if c.personal_kind} == {
        "name",
        "phone",
        "email",
        "pan",
        "bank_account",
        "address",
    }
    # statistics exist for non-personal columns only
    assert all(not c.stats for c in emp.columns if c.personal_kind)
    assert emp.columns[0].stats["distinct"] == 2140


def test_scan_never_selects_personal_columns(dw_scan):
    """Guard 1 (docs/11): grep every logged query for personal columns of the tables it reads."""
    _, log = dw_scan
    personal = {(t, c.lower()) for src, _s, t, c in extras.PERSONAL if src == "sales_dw"}
    assert log.entries
    for q in log.entries:
        ids = identifiers(q.sql)
        for table, col in personal:
            assert not (table in ids and col in ids), q.sql
    # and no query reads table rows: every statement is an aggregate or a catalog read
    for q in log.entries:
        assert re.match(r"^SELECT (count|c\.table_name|c\.relname|table_name)", q.sql), q.sql


def test_erp_db_scan(tmp_path):
    log = QueryLog()
    tables = {t.name: t for t in scan(my("othor_ro"), log=log, today=TODAY)}
    assert len(tables) == 64
    cust = tables["customers"]
    assert cust.sorted.use == "dimension"
    assert {c.name for c in cust.columns if c.personal_kind} == {"contact_email", "contact_phone"}
    assert tables["invoices"].sorted.use == "metric_source"
    assert tables["invoices"].watermark == "updated_at"
    assert tables["audit_log"].sorted.reason == "log"
    for q in log.entries:
        ids = identifiers(q.sql)
        assert not ("customers" in ids and {"contact_email", "contact_phone"} & ids), q.sql


def test_skip_schema_stops_it():
    log = QueryLog()
    got = list(scan(pg("othor_ro"), log=log, today=TODAY, skip=lambda s: s == "sales_dw"))
    assert {t.schema for t in got} == {"public"}
