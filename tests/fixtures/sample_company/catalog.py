# SPDX-License-Identifier: Apache-2.0
"""The scanned catalog of the sample sources, built from the generator's frames (no databases).

Writes `source`, `source_table` and `source_column` rows the way an M2 scan would: names and
types from the frames, uses and personal columns from the M2 rules. The compiler resolves
the sample Ontology against these rows in unit tests; integration tests use a real scan.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from othor.connectors.classify import Col, TableFacts, classify, personal_columns, stable_key
from othor.connectors.models import Source, SourceColumn, SourceTable
from othor.connectors.sheets.detect import detect, tab_use
from tests.fixtures.sample_company.generate import SampleCompany

TODAY = dt.date(2026, 11, 4)


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


def _source(s: Session, kind: str, name: str, config: dict[str, Any]) -> Source:
    src = Source(kind=kind, name=name, status="connected", config=config, in_setup=True)
    s.add(src)
    s.flush()
    return src


def _table(s: Session, src: Source, schema: str, name: str, df: pd.DataFrame, key: str) -> None:
    cols = tuple(Col(str(c), _kind(df[c])) for c in df.columns)
    facts = TableFacts(schema, name, cols, len(df), None)
    sorted_ = classify(facts, TODAY)
    personal = dict(personal_columns(facts))
    t = SourceTable(
        source_id=src.id,
        schema=schema,
        name=name,
        state="done",
        row_count=len(df),
        use=sorted_.use,
        use_reason=sorted_.reason,
        scan_use=sorted_.use,
        scan_reason=sorted_.reason,
        stable_key=key,
        personal_count=len(personal),
    )
    s.add(t)
    s.flush()
    for i, c in enumerate(cols):
        s.add(
            SourceColumn(
                table_id=t.id,
                position=i,
                name=c.name,
                data_type=c.kind,
                kind=c.kind,
                is_personal=c.name in personal,
                personal_kind=personal.get(c.name),
            )
        )


def catalog_from_generator(
    s: Session,
    company: SampleCompany,
    *,
    sheets: bool | tuple[str, ...] = False,
    erp_schema: str = "erp_db",
) -> dict[str, uuid.UUID]:
    """Inside a tenant scope. Returns source name → id. `sheets`: True for the Order tracker, or
    the titles of the sample spreadsheets to add ("Order tracker FY27", "MIS-Weekly").
    `erp_schema`: the MySQL database the erp_db source reads (erp_e2e for the e2e nights)."""
    out = {}
    erp = _source(s, "mysql", "erp_db", {"database": erp_schema, "host": "erp-db"})
    for name, df in sorted(company.erp.items()):
        _table(s, erp, erp_schema, name, df, stable_key(erp_schema, name))
    out["erp_db"] = erp.id
    dw = _source(
        s, "postgres", "sales_dw", {"database": "sales_dw", "schemas": ["public", "sales_dw"]}
    )
    for schema, tables in sorted(company.dw.items()):
        for name, df in sorted(tables.items()):
            _table(s, dw, schema, name, df, stable_key(schema, name))
    out["sales_dw"] = dw.id
    titles = ("Order tracker FY27",) if sheets is True else (sheets or ())
    for want in titles:
        doc = next(d for d in company.sheets.values() if d["properties"]["title"] == want)
        title = doc["properties"]["title"]
        gs = _source(s, "gsheets", title, {"files": [{"id": doc["spreadsheetId"], "title": title}]})
        for sheet in doc["sheets"]:
            shape = detect(sheet, today=TODAY)
            use = tab_use(shape)
            name = sheet["properties"]["title"]
            t = SourceTable(
                source_id=gs.id,
                schema=title,
                name=name,
                kind="tab",
                state="done",
                row_count=shape.row_count,
                use=use.use,
                use_reason=use.reason,
                scan_use=use.use,
                stable_key=stable_key(doc["spreadsheetId"], name),
            )
            s.add(t)
            s.flush()
            personal = dict(use.personal)
            for i, c in enumerate(shape.data_columns):
                kind = {"date": "date", "number": "decimal"}.get(c.kind, "text")
                s.add(
                    SourceColumn(
                        table_id=t.id,
                        position=i,
                        name=c.header,
                        data_type=c.kind,
                        kind=kind,
                        is_personal=c.header in personal,
                        personal_kind=personal.get(c.header),
                    )
                )
        out[title] = gs.id
    s.flush()
    return out
