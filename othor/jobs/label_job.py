# SPDX-License-Identifier: Apache-2.0
"""Label what each table "looks like" with the model (docs/06 "Scan"), after the scan has
finished with rules labels. Optional: with no model, or if a label fails a guard, the rules
label stays. Input is names, types, row counts and date ranges only; Excluded tables and every
personal column are left out.

payload: {"source_id": str, "scan_id": str}
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa

from othor.ai.gateway import ModelGateway
from othor.ai.guards import numbers_grounded
from othor.connectors.models import SourceColumn, SourceTable
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext

PROMPT = ("scan_table_label", "v1")
BATCH = 50
MAX_COLUMNS = 25
MAX_LABEL = 80


def table_input(t: SourceTable, cols: list[SourceColumn]) -> dict[str, Any]:
    out_cols = []
    for c in cols:
        if c.is_personal:
            continue  # never named to a model
        col: dict[str, Any] = {"column": c.name, "type": c.kind}
        st = c.stats or {}
        if st.get("distinct") is not None:
            col["distinct"] = st["distinct"]
        if c.kind in ("date", "timestamp") and st.get("min"):
            col["from"], col["to"] = str(st["min"])[:10], str(st.get("max") or "")[:10]
        out_cols.append(col)
        if len(out_cols) == MAX_COLUMNS:
            break
    return {
        "key": t.stable_key,
        "table": f"{t.schema}.{t.name}",
        "row_count": t.row_count,
        "sorted_as": t.use if t.use != "ignored" else f"ignored ({t.use_reason})",
        "columns": out_cols,
    }


def run(ctx: WorkerContext, job: Job) -> None:
    gw: ModelGateway | None = ctx.gateway
    if gw is None or gw.provider.name == "template":
        return
    source_id = uuid.UUID(job.payload["source_id"])
    with ctx.sf() as s:
        tables = s.scalars(
            sa.select(SourceTable)
            .where(
                SourceTable.source_id == source_id,
                SourceTable.gone_at.is_(None),
                SourceTable.state == "done",
                SourceTable.use != "excluded",
                sa.or_(SourceTable.looks_like_by.is_(None), SourceTable.looks_like_by == "rules"),
            )
            .order_by(SourceTable.schema, SourceTable.name)
        ).all()
        cols: dict[uuid.UUID, list[SourceColumn]] = {}
        for c in s.scalars(
            sa.select(SourceColumn)
            .where(SourceColumn.table_id.in_([t.id for t in tables]))
            .order_by(SourceColumn.position)
        ):
            cols.setdefault(c.table_id, []).append(c)
        personal = tuple(sorted({c.name for cs in cols.values() for c in cs if c.is_personal}))
        company_id = tables[0].company_id if tables else None
        items = [(t.id, table_input(t, cols.get(t.id, []))) for t in tables]
    if company_id is None:
        return
    for i in range(0, len(items), BATCH):
        batch = items[i : i + BATCH]
        inputs = {"tables": [x for _, x in batch]}
        res = gw.complete(company_id, *PROMPT, inputs, personal_names=personal)
        if not res.ok or not res.output:
            continue
        by_key = {x["key"]: (tid, x) for tid, x in batch}
        with ctx.sf() as s:
            for lab in res.output.get("labels", []):
                hit = by_key.get(str(lab.get("key")))
                text = str(lab.get("label", "")).strip()
                if hit is None or not text or len(text) > MAX_LABEL:
                    continue
                tid, tin = hit
                if not numbers_grounded(text, tin):
                    continue  # a number the input doesn't have: keep the rules label
                row = s.scalars(sa.select(SourceTable).where(SourceTable.id == tid)).one()
                row.looks_like, row.looks_like_by = text, "ai"
            s.commit()
