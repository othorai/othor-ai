# SPDX-License-Identifier: Apache-2.0
"""Sample-company fixtures for connector unit tests (generator only; no databases)."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from othor.connectors.classify import Col, TableFacts
from tests.fixtures.sample_company.generate import SampleCompany, generate

TODAY = dt.date(2026, 11, 4)


@pytest.fixture(scope="session")
def company() -> SampleCompany:
    return generate()


def _kind(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "bool"
    if pd.api.types.is_integer_dtype(s):
        return "int"
    if pd.api.types.is_float_dtype(s):
        return "decimal"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "timestamp"
    first = next((v for v in s if v is not None and v == v), None)
    if isinstance(first, pd.Timestamp | dt.datetime):
        return "timestamp"
    if isinstance(first, dt.date):
        return "date"
    return "text"


def facts(schema: str, name: str, df: pd.DataFrame) -> TableFacts:
    """What the scan would know about a table: names, types, row count, newest business date."""
    cols = tuple(Col(str(c), _kind(df[c])) for c in df.columns)
    newest: dt.date | None = None
    for c in cols:
        if c.kind in ("date", "timestamp") and not c.is_audit and len(df):
            v = pd.to_datetime(df[c.name]).max()
            if v == v:
                d = v.date()
                newest = d if newest is None or d > newest else newest
    return TableFacts(schema, name, cols, len(df), newest)


@pytest.fixture(scope="session")
def all_tables(company: SampleCompany) -> dict[str, TableFacts]:
    out = {f"erp_db.{n}": facts("erp_db", n, df) for n, df in company.erp.items()}
    for schema, tables in company.dw.items():
        out |= {f"{schema}.{n}": facts(schema, n, df) for n, df in tables.items()}
    return out
