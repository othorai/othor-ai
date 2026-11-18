# SPDX-License-Identifier: Apache-2.0
"""A report's read of rows (Look back v2, LB7, 18 Nov 2026): only the columns named, through the
confirmed join rules, never a personal column, within the window and the cap; a stacked table in
the same query; and the SQL runs, giving the sample's own rows."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import duckdb
import pytest
import sqlglot

from othor.ontology.compiler import PersonalColumnRejected
from othor.ontology.compiler.render import CompileError
from othor.ontology.compiler.rows import Pick, Stack, rows_sql
from tests.fixtures.sample_company.ontology import ERP, T
from tests.unit.ontology.test_compiler import _records

INV = T(ERP, "erp_db", "invoices")
CN = T(ERP, "erp_db", "credit_notes")
PICKS = [
    Pick("invoice_date", "invoice_date"),
    Pick("net_amount", "net_amount"),
    Pick("customer", "customer_code", "customers"),
    Pick("region", "region", "customers"),
]


def _read(world, dialect="duckdb", **kw):  # type: ignore[no-untyped-def]
    records, cat, _ = _records(world)
    return rows_sql(records, cat, INV, PICKS, "invoice_date", dialect, limit=500_001, **kw)


def _columns(sql: str, dialect: str) -> set[str]:
    tree = sqlglot.parse_one(sql.replace(":", "__p_") if dialect != "duckdb" else sql, read=dialect)
    return {c.name for c in tree.find_all(sqlglot.exp.Column) if not c.name.startswith("__p_")}


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "duckdb"])
def test_only_the_named_columns_through_the_join_rule(world, dialect) -> None:  # type: ignore[no-untyped-def]
    r = _read(world, dialect)
    cols = _columns(r.sql, dialect)
    # the plan's columns, the join keys, and the fact table's own rules (cancelled invoices out)
    assert {"invoice_date", "net_amount", "customer_code", "region", "customer_id"} <= cols
    assert cols <= {
        "invoice_date",
        "net_amount",
        "customer_code",
        "region",
        "customer_id",
        "status",
    }
    assert "LEFT JOIN" in r.sql.upper() and "LIMIT 500001" in r.sql.upper()
    assert r.tables == ["invoices", "customers"]
    assert r.columns["customers"] == ["customer_code", "customer_id", "region"]


def test_a_personal_column_is_refused(world) -> None:  # type: ignore[no-untyped-def]
    records, cat, _ = _records(world)
    with pytest.raises(PersonalColumnRejected):
        rows_sql(
            records,
            cat,
            INV,
            [*PICKS, Pick("email", "contact_email", "customers")],
            "invoice_date",
            "duckdb",
            limit=10,
        )


def test_a_table_with_no_confirmed_join_is_refused(world) -> None:  # type: ignore[no-untyped-def]
    records, cat, _ = _records(world)
    with pytest.raises(CompileError, match="no join rule"):
        rows_sql(
            records,
            cat,
            INV,
            [*PICKS, Pick("item", "item_code", "items")],
            "invoice_date",
            "duckdb",
            limit=10,
        )


def test_bad_names_are_refused(world) -> None:  # type: ignore[no-untyped-def]
    records, cat, _ = _records(world)
    with pytest.raises(CompileError):
        rows_sql(records, cat, INV, [Pick("Bad Name", "net_amount")], "x", "duckdb", limit=10)
    with pytest.raises(CompileError, match="date"):
        rows_sql(records, cat, INV, PICKS, "not_there", "duckdb", limit=10)


def test_credit_notes_stack_under_invoices(world) -> None:  # type: ignore[no-untyped-def]
    stack = Stack(
        CN, {"invoice_date": "cn_date", "net_amount": "net_amount"}, negate=("net_amount",)
    )
    r = _read(world, "postgres", stack=stack)
    assert '-("t"."net_amount") AS "net_amount"' in " ".join(r.sql.split()).replace(
        "( ", "("
    ).replace(" )", ")")
    assert "UNION ALL" in r.sql.upper()
    assert "'Sale' AS \"kind\"" in r.sql and "'Return' AS \"kind\"" in r.sql
    assert r.tables == ["invoices", "customers", "credit_notes"]
    # customer code reaches credit notes by their own confirmed rule; nothing is NULL here
    assert r.sql.upper().count("LEFT JOIN") == 2


def _duck(sample) -> duckdb.DuckDBPyConnection:  # type: ignore[no-untyped-def]
    con = duckdb.connect()
    con.execute("CREATE SCHEMA erp_db")
    for name in ("invoices", "credit_notes", "customers"):
        con.register(f"_{name}", sample.erp[name])
        con.execute(f"CREATE TABLE erp_db.{name} AS SELECT * FROM _{name}")
    return con


def test_the_read_runs_and_matches_the_sample(world, sample) -> None:  # type: ignore[no-untyped-def]
    stack = Stack(CN, {"invoice_date": "cn_date", "net_amount": "net_amount"})
    r = _read(world, "duckdb", stack=stack)
    con = _duck(sample)
    lo, hi = dt.date(2025, 4, 1), dt.date(2026, 4, 1)
    got = con.execute(r.sql, {"from": lo, "to": hi}).fetchall()
    inv = sample.erp["invoices"]
    d = inv["invoice_date"].astype("datetime64[ns]")
    keep = (d >= str(lo)) & (d < str(hi)) & (inv["status"].str.lower() != "cancelled")
    sales = [row for row in got if row[-1] == "Sale"]
    assert len(sales) == int(keep.sum())
    want = sum(Decimal(str(v)) for v in inv.loc[keep, "net_amount"])
    assert sum(Decimal(str(row[1])) for row in sales) == want
    assert any(row[-1] == "Return" for row in got)
    assert all(row[3] is not None for row in got)  # every row has its region


def test_the_cap_is_one_more_than_allowed(world) -> None:  # type: ignore[no-untyped-def]
    records, cat, _ = _records(world)
    r = rows_sql(records, cat, INV, PICKS, "invoice_date", "duckdb", limit=3)
    assert r.sql.upper().rstrip().endswith("LIMIT 3")


def test_a_filter_on_a_joined_table_brings_its_join(world) -> None:  # type: ignore[no-untyped-def]
    from tests.fixtures.sample_company.ontology import col, lit, op

    records, cat, _ = _records(world)
    export = [op("=", col("region", "customers"), lit("Export"))]
    r = rows_sql(
        records,
        cat,
        INV,
        PICKS[:2],
        "invoice_date",
        "mysql",
        filters=export,
        limit=10,
        stack=Stack(
            CN,
            {"invoice_date": "cn_date", "net_amount": "net_amount"},
            filters=export,
            negate=("net_amount",),
        ),
    )
    assert r.sql.upper().count("LEFT JOIN") == 2  # customers, for the invoices and the notes
