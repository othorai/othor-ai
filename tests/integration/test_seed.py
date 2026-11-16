# SPDX-License-Identifier: Apache-2.0
"""`othor seed` loads the sample company into the compose databases (needs `make dev`)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.conftest import require_services
from tests.fixtures.sample_company.generate import SampleCompany, generate
from tests.fixtures.sample_company.load import load_all

ERP_ADMIN = os.environ.get(
    "OTHOR_SAMPLE_MYSQL_URL", "mysql+pymysql://root:root_dev@localhost:3307/erp_db"
)
DW_ADMIN = os.environ.get(
    "OTHOR_SAMPLE_PG_URL", "postgresql+psycopg://dw_admin:dw_admin_dev@localhost:5443/sales_dw"
)
ERP_RO = "mysql+pymysql://othor_ro:othor_ro_dev@localhost:3307/erp_db"
DW_RO = "postgresql+psycopg://othor_ro:othor_ro_dev@localhost:5443/sales_dw"
DW_RW = "postgresql+psycopg://othor_rw:othor_rw_dev@localhost:5443/sales_dw"


def _reachable(url: str) -> bool:
    try:
        eng = sa.create_engine(url, connect_args={"connect_timeout": 2})
        with eng.connect():
            pass
        eng.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> tuple[SampleCompany, dict[str, int], Path]:
    if not (_reachable(ERP_ADMIN) and _reachable(DW_ADMIN)):
        require_services("sample sources erp_db (localhost:3307) and sales_dw (localhost:5443)")
    company = generate()
    sheets = tmp_path_factory.mktemp("sheets")
    counts = load_all(company, mysql_url=ERP_ADMIN, pg_url=DW_ADMIN, sheets_dir=sheets)
    return company, counts, sheets


def _count(url: str, table: str) -> int:
    eng = sa.create_engine(url)
    with eng.connect() as c:
        n = c.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
    eng.dispose()
    return int(n)


def test_row_counts_match_generator(seeded: tuple[SampleCompany, dict[str, int], Path]) -> None:
    company, counts, _ = seeded
    for name in ("invoices", "receivables", "customers", "cost_ledger"):
        assert _count(ERP_ADMIN, name) == len(company.erp[name]) == counts[f"erp_db.{name}"]
    for schema, name in (
        ("sales_dw", "fact_invoice"),
        ("sales_dw", "fact_dispatch"),
        ("public", "employee"),
    ):
        assert _count(DW_ADMIN, f"{schema}.{name}") == len(company.dw[schema][name])
    eng = sa.create_engine(ERP_ADMIN)
    with eng.connect() as c:
        assert len(c.execute(sa.text("SHOW TABLES")).all()) == 64
    eng.dispose()


def test_amounts_survive_the_load(seeded: tuple[SampleCompany, dict[str, int], Path]) -> None:
    company, _, _ = seeded
    eng = sa.create_engine(ERP_ADMIN)
    with eng.connect() as c:
        total = c.execute(
            sa.text(
                "SELECT sum(net_amount) FROM invoices WHERE invoice_date >= '2026-10-01' "
                "AND invoice_date < '2026-11-01'"
            )
        ).scalar_one()
    eng.dispose()
    inv = company.erp["invoices"]
    expected = inv.loc[
        [d.year == 2026 and d.month == 10 for d in inv["invoice_date"]], "net_amount"
    ].sum()
    assert abs(float(total) - expected) < 0.01


def test_reseed_is_idempotent(seeded: tuple[SampleCompany, dict[str, int], Path]) -> None:
    company, counts, sheets = seeded
    again = load_all(company, mysql_url=ERP_ADMIN, pg_url=DW_ADMIN, sheets_dir=sheets)
    assert again == counts


def test_read_only_role_reads_but_cannot_write(
    seeded: tuple[SampleCompany, dict[str, int], Path],
) -> None:
    assert _count(ERP_RO, "invoices") > 0
    assert _count(DW_RO, "sales_dw.fact_invoice") > 0
    for url, stmt in (
        (ERP_RO, "INSERT INTO fx_rates (rate_date, eur_inr) VALUES ('2030-01-01', 1)"),
        (DW_RO, "INSERT INTO public.dim_region VALUES (9, 'Nowhere')"),
    ):
        eng = sa.create_engine(url)
        with pytest.raises(sa.exc.DBAPIError, match=r"(?i)denied"), eng.begin() as c:
            c.execute(sa.text(stmt))
        eng.dispose()


def test_writer_role_exists_for_the_refusal_test(
    seeded: tuple[SampleCompany, dict[str, int], Path],
) -> None:
    eng = sa.create_engine(DW_RW)
    with eng.connect() as c:
        can = c.execute(
            sa.text("SELECT has_table_privilege('othor_rw', 'public.dim_region', 'INSERT')")
        ).scalar_one()
    eng.dispose()
    assert can is True


def test_sheets_written(seeded: tuple[SampleCompany, dict[str, int], Path]) -> None:
    _, _, sheets = seeded
    index = json.loads((sheets / "index.json").read_text())
    assert {i["title"] for i in index} == {"MIS-Weekly", "Order tracker FY27"}
    assert json.loads((sheets / "auth.json").read_text())["revoked_at"] is None


def test_large_scale_loads_and_default_seed_removes_it(
    seeded: tuple[SampleCompany, dict[str, int], Path],
) -> None:
    """SCALE=large shrunk for the test: real shape, few rows, 30 stress tables."""
    from tests.fixtures.sample_company.load import _drop_large, load_large

    company, _, _ = seeded
    counts = load_large(company, DW_ADMIN, row_cap=2_000, stress_tables=30)
    try:
        assert _count(DW_RO, "perf.fact_invoice") == 2_000
        assert _count(DW_RO, "public.etl_log") == 2_000
        assert sum(k.startswith("sales_dw.scan_stress.") for k in counts) == 30
        assert _count(DW_ADMIN, "sales_dw.fact_invoice") == len(
            company.dw["sales_dw"]["fact_invoice"]
        )
    finally:
        _drop_large(DW_ADMIN)
        load_all(
            company, mysql_url=ERP_ADMIN, pg_url=DW_ADMIN, sheets_dir=seeded[2]
        )  # back to the default shape
    eng = sa.create_engine(DW_ADMIN)
    with eng.connect() as c:
        left = c.execute(
            sa.text("SELECT count(*) FROM pg_namespace WHERE nspname IN ('perf', 'scan_stress')")
        ).scalar_one()
    eng.dispose()
    assert left == 0
