# SPDX-License-Identifier: Apache-2.0
"""Read a tab with its confirmed shape into typed columns (docs/05 "Nightly", 10c).

Totals and title rows are left out, merged cells filled down, blank and notes columns dropped,
dates parsed from each cell's own format, and amounts in "(₹ L)" / "(₹ Cr)" headers converted to
rupees with Decimal arithmetic (same inputs, same numbers).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from othor.connectors.sheets.detect import (
    _YEARLESS,
    UNIT_FACTOR,
    TabShape,
    _grid,
    _year_hint,
    blankish,
    date_format,
    order_years,
    parse_date,
    parse_number,
    row_years,
)

_UNIT_TEXT = re.compile(r"\s*\([^)]*\)\s*")


def column_name(header: str) -> str:
    """'Value (₹ L)' → 'value', 'Expected invoice' → 'expected_invoice'."""
    base = _UNIT_TEXT.sub(" ", header).strip().lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return base or "column"


def header_fingerprint(s: TabShape) -> str:
    """Changes when a column is renamed, added, removed or moved, or the header row moves."""
    parts = [f"h{s.header_row}"] + [f"{c.letter}={c.header}" for c in s.columns if c.header]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _decimal(v: float | None, raw: str) -> Decimal | None:
    if v is None:
        return None
    # from the cell text, not the float, so 21.6 stays exactly 21.6
    t = re.sub(r"[₹$€,()%\s]", "", raw.strip().replace("−", "-"))
    d = Decimal(t)
    return -d if raw.strip().startswith("(") else d


def extract(
    sheet: dict[str, Any],
    s: TabShape,
    *,
    skip: set[str] | frozenset[str] = frozenset(),
    today: dt.date | None = None,
) -> pd.DataFrame:
    """The tab's table, typed. `skip`: headers never read (personal columns); `today`: the
    run's date, for dates written without a year (never the wall clock in a run)."""
    if s.kind != "table" or s.first_row is None or s.last_row is None:
        raise ValueError(f"tab {s.tab!r} has no table to read ({s.kind})")
    grid = _grid(sheet)
    hint = _year_hint(grid, s.title_rows)
    year = hint or (today or dt.date.today()).year
    rows = [r - 1 for r in range(s.first_row, s.last_row + 1) if r not in s.total_rows]
    fill = {(letter, row) for letter, row in s.filled_down}
    row_year = _row_years(grid, s, rows, today, hint is not None)
    out: dict[str, list[Any]] = {}
    used: set[str] = set()
    for idx, c in enumerate(s.columns):
        if c.kind == "blank" or c.letter in s.notes_cols or c.header in skip:
            continue
        name = column_name(c.header)
        while name in used:
            name += "_2"
        used.add(name)
        values: list[Any] = []
        prev: Any = None
        for r in rows:
            raw = grid[r][idx] if idx < len(grid[r]) else ""
            if (c.letter, r + 1) in fill and not raw.strip():
                values.append(prev)
                continue
            if blankish(raw):
                v: Any = None
            elif c.kind == "date":
                f = date_format(raw)
                v = parse_date(raw, f, year=row_year.get(r, year)) if f else None
            elif c.kind == "number":
                v = _decimal(parse_number(raw), raw)
                if v is not None and c.unit in UNIT_FACTOR:
                    v = v * UNIT_FACTOR[c.unit]
            else:
                v = raw.strip()
            values.append(v)
            prev = v
        out[name] = values
    return pd.DataFrame(out)


def _row_years(
    grid: list[list[str]], s: TabShape, rows: list[int], today: dt.date | None, hinted: bool
) -> dict[int, int]:
    """Each row's year for dates written without one: the row's own dated cell ("Oct 2026"),
    else, with no year in the tab's title, the tab's order (`detect.order_years`)."""
    dated = [i for i, c in enumerate(s.columns) if c.kind == "date"]
    out = row_years(grid, rows, dated)
    if hinted:
        return out
    for i, c in enumerate(s.columns):
        if c.kind == "date" and set(c.date_formats) & _YEARLESS:
            years, _ = order_years(grid, rows, i, out, today or dt.date.today())
            out = {**years, **out}
            break
    return out


def load_duckdb(path: Path, table: str, df: pd.DataFrame) -> int:
    """Replace one tab's table in the company's analytics store. Returns rows written."""
    import duckdb

    path.parent.mkdir(parents=True, exist_ok=True)
    q = '"' + table.replace('"', '""') + '"'
    con = duckdb.connect(str(path))
    try:
        con.register("incoming", df)
        con.execute(f"CREATE OR REPLACE TABLE {q} AS SELECT * FROM incoming")  # noqa: S608
        con.unregister("incoming")
        n = con.execute(f"SELECT count(*) FROM {q}").fetchone()  # noqa: S608
        return int(n[0]) if n else 0
    finally:
        con.close()
