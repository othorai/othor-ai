# SPDX-License-Identifier: Apache-2.0
"""SCALE=large plans the volume without touching the default data (no database needed)."""

import pandas as pd

from tests.fixtures.sample_company import large
from tests.fixtures.sample_company.generate import SampleCompany


def test_board_row_counts(company: SampleCompany) -> None:
    tables = {(t.schema, t.name): t.rows for t in large.big_tables(company)}
    assert tables[("sales_dw", "fact_invoice_bak_2023")] == 388_201
    assert tables[("public", "etl_log")] == 1_212_880
    assert tables[("perf", "fact_invoice")] == 1_204_118
    assert tables[("perf", "fact_dispatch")] == 3_104_220


def test_chunks_cycle_real_rows_with_fresh_keys(company: SampleCompany) -> None:
    bt = next(t for t in large.big_tables(company, row_cap=None) if t.name == "fact_invoice")
    bt.rows = len(bt.template) * 2 + 10
    df = pd.concat(bt.chunks(size=7_000), ignore_index=True)
    assert len(df) == bt.rows
    assert df["invoice_key"].is_unique and df["invoice_no"].is_unique
    n = len(bt.template)
    assert df["net_value"].iloc[n] == bt.template["net_value"].iloc[0]  # values are real rows


def test_stress_schema_has_4000_tables() -> None:
    tables = dict(large.stress_tables(seed=20261101))
    assert len(tables) == 4_000
    assert all(len(name) <= 63 for name in tables)
    assert sum(len(df) == 0 for df in tables.values()) > 100
    assert any({"name", "phone"} <= set(df.columns) for df in tables.values())


def test_default_data_is_untouched(company: SampleCompany, expected: dict) -> None:  # type: ignore[type-arg]
    large.big_tables(company)
    assert company.data_hash() == expected["data_hash"]
