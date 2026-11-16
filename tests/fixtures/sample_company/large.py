# SPDX-License-Identifier: Apache-2.0
"""`make seed SCALE=large`: volume for performance and stress tests (M2 scan, M11).

The default seed stays small (about 20k invoice rows) so `make seed` and CI stay fast. Large
mode leaves every table the planted patterns use exactly as the default seed has them (same
data hash). It adds three things, all streamed into PostgreSQL `sales_dw`:

1. The ignored, backup and log tables at the row counts the scan boards show (2k–2kd).
2. Schema `perf`: copies of the fact tables at board sizes, for read and materialise timing.
   Rows cycle through the real sample rows with fresh keys, so values stay realistic.
3. Schema `scan_stress`: 4,000 small tables (some empty, some backups and logs, some with
   personal-looking column names) for the scan layout at 4,000 tables.

Screens show whatever the scan computes; nothing here is tuned to a board number.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from tests.fixtures.sample_company import company as co
from tests.fixtures.sample_company.generate import SampleCompany

# Row counts from the scan boards 2k–2kd.
IN_PLACE = {
    ("sales_dw", "fact_invoice_bak_2023"): 388_201,
    ("sales_dw", "fact_invoice_2019"): 402_113,
    ("public", "stg_orders"): 2_906_004,
    ("public", "etl_log"): 1_212_880,
    ("public", "invoice_print_log"): 2_880_113,
}
PERF = {
    "fact_invoice": 1_204_118,
    "fact_invoice_line": 6_880_342,
    "fact_order": 2_906_118,
    "fact_dispatch": 3_104_220,
}
STRESS_TABLES = 4_000
KEY_COLS = {"invoice_key", "order_key", "dispatch_key", "run_id", "print_id"}
NO_COLS = {"invoice_no", "order_no"}


@dataclass
class BigTable:
    schema: str
    name: str
    template: pd.DataFrame
    rows: int

    def chunks(self, size: int = 50_000) -> Iterator[pd.DataFrame]:
        """Cycle the template rows; key columns are renumbered, document numbers suffixed."""
        t = self.template.reset_index(drop=True)
        done = 0
        while done < self.rows:
            n = min(size, self.rows - done)
            idx = np.arange(done, done + n) % len(t)
            lap = np.arange(done, done + n) // len(t)
            df = t.iloc[idx].reset_index(drop=True)
            for c in df.columns:
                if c in KEY_COLS:
                    df[c] = np.arange(done + 1, done + n + 1)
                elif c in NO_COLS:
                    df[c] = [
                        v if (k == 0 or v is None) else f"{v}~{k}"
                        for v, k in zip(df[c], lap, strict=True)
                    ]
            done += n
            yield df


def big_tables(company: SampleCompany, row_cap: int | None = None) -> list[BigTable]:
    out = []
    for (schema, name), n in IN_PLACE.items():
        out.append(BigTable(schema, name, company.dw[schema][name], min(n, row_cap or n)))
    for name, n in PERF.items():
        out.append(BigTable("perf", name, company.dw["sales_dw"][name], min(n, row_cap or n)))
    return out


def stress_tables(seed: int, n: int = STRESS_TABLES) -> Iterator[tuple[str, pd.DataFrame]]:
    """scan_stress.t0001 … t4000: a mix of facts, lists, empties, backups, logs, personal."""
    words = [
        "sales",
        "stock",
        "orders",
        "returns",
        "ledger",
        "budget",
        "plant",
        "vendor",
        "quality",
        "shift",
        "machine",
        "yarn",
        "dye",
        "price",
        "route",
        "claim",
    ]
    kinds = ["fact", "list", "empty", "backup", "log", "personal"]
    for i in range(1, n + 1):
        r = co.stream(seed, "stress", i)
        kind = kinds[int(r.choice(len(kinds), p=[0.35, 0.25, 0.12, 0.1, 0.12, 0.06]))]
        w = words[i % len(words)]
        name = {
            "backup": f"{w}_bak_{2019 + i % 6}",
            "log": f"{w}_log_{i:04d}",
            "empty": f"tmp_{w}_{i:04d}",
        }.get(kind, f"{w}_{kind}_{i:04d}")
        rows = 0 if kind == "empty" else int(r.integers(1, 400))
        cols: dict[str, Any] = {"id": np.arange(1, rows + 1)}
        if kind in ("fact", "backup"):
            cols["entry_date"] = [
                co.START + pd.Timedelta(days=int(x)).to_pytimedelta()
                for x in r.integers(0, 2000, rows)
            ]
            cols["amount"] = np.round(r.lognormal(10, 1, rows), 2)
        elif kind == "log":
            cols["logged_at"] = pd.Timestamp("2025-01-01") + pd.to_timedelta(
                np.sort(r.integers(0, 3e7, rows)), unit="s"
            )
        elif kind == "personal":
            cols["name"] = [f"Person {k}" for k in range(rows)]
            cols["phone"] = [f"+91 9{k:09d}" for k in range(rows)]
        else:
            cols["code"] = [f"{w[:3].upper()}{k:04d}" for k in range(rows)]
            cols["label"] = [f"{w} {k}" for k in range(rows)]
        if rows == 0:
            cols = {"id": pd.Series([], dtype="int64"), "payload": pd.Series([], dtype="str")}
        yield f"t{i:04d}_{name}"[:63], pd.DataFrame(cols)
