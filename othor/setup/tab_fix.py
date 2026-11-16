# SPDX-License-Identifier: Apache-2.0
"""Fix a sheet tab with Othor AI (M4b: 10c). The owner says in a line what's wrong ("values are in
₹ crore"); the proposal is a typed shape change (header row, last row, a column's unit), never a
value. Apply re-reads the tab by code (the same detect with the owner's overrides) and the check
page shows what was read. Without the model, the units form and the stepper work the same.

Inputs to the model: the tab's headers, kinds and detected units, its header and last rows. No
cell values.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.connectors.models import SheetTab
from othor.connectors.sheets.detect import UNIT_FACTOR
from othor.core.events import record_event
from othor.setup.models import ChatMessage, ChatThread

PURPOSE = "tab_fix"
UNITS = [("inr", "₹"), ("thousand", "₹ thousand"), ("lakh", "₹ lakh"), ("crore", "₹ crore")]
UNIT_NAME = dict(UNITS)


def thread(s: Session, tab_id: uuid.UUID, *, create: bool = True) -> ChatThread | None:
    subject = f"tab:{tab_id}"
    t = s.scalars(
        sa.select(ChatThread).where(
            ChatThread.subject == subject,
            ChatThread.purpose == PURPOSE,
            ChatThread.status == "open",
        )
    ).first()
    if t is None and create:
        t = ChatThread(purpose=PURPOSE, subject=subject)
        s.add(t)
        s.flush()
    return t


def number_columns(tab: SheetTab) -> list[str]:
    return [c["header"] for c in (tab.shape or {}).get("columns", []) if c.get("kind") == "number"]


def model_inputs(tab: SheetTab, history: list[ChatMessage]) -> dict[str, Any]:
    shape = tab.shape or {}
    return {
        "tab": tab.tab_name,
        "header_row": shape.get("header_row"),
        "last_row": shape.get("last_row"),
        "grid_rows": shape.get("grid_rows"),
        "columns": [
            {"header": c.get("header"), "kind": c.get("kind"), "unit": c.get("unit")}
            for c in shape.get("columns", [])
        ],
        "units": [u for u, _ in UNITS],
        "conversation": [{"role": m.role, "text": m.text} for m in history[-10:]],
    }


def proposal(out: dict[str, Any], tab: SheetTab, inputs: Any = None) -> dict[str, Any] | None:
    """The model's shape change, checked: rows in range, a known column, a known unit, and (with
    `inputs`) no row number the owner or the tab didn't give."""
    p = dict(out.get("patch") or {})
    if inputs is not None:
        from othor.ai.guards import numerals

        allowed = numerals(json.dumps(inputs, ensure_ascii=False, default=str))
        for k in ("header_row", "last_row"):
            if p.get(k) and str(p[k]) not in allowed:
                p[k] = 0
    shape = tab.shape or {}
    grid = int(shape.get("grid_rows") or 0)
    got: dict[str, Any] = {"header_row": shape.get("header_row") or 1}
    lines: list[str] = []
    hr = p.get("header_row")
    if isinstance(hr, int) and 1 <= hr <= min(50, grid or 50) and hr != shape.get("header_row"):
        got["header_row"] = hr
        lines.append(f"Header row {shape.get('header_row')} → {hr}")
    lr = p.get("last_row")
    if isinstance(lr, int) and got["header_row"] < lr <= (grid or lr):
        got["last_row"] = lr
        lines.append(f"Last row {lr}")
    numbers = set(number_columns(tab))
    for u in p.get("units") or []:
        col, unit = str(u.get("column") or ""), str(u.get("unit") or "")
        if col in numbers and unit in UNIT_FACTOR:
            got["unit_col"], got["unit"] = col, unit
            lines.append(f"{col}: read as {UNIT_NAME[unit]}")
            break
    if not lines:
        return None
    return {"shape": got, "lines": lines}


def mark_applied(s: Session, message_id: str, *, actor: str) -> None:
    try:
        mid = uuid.UUID(message_id)
    except ValueError:
        return
    m = s.scalars(sa.select(ChatMessage).where(ChatMessage.id == mid)).first()
    if m is None:
        return
    m.applied = True
    t = s.scalars(sa.select(ChatThread).where(ChatThread.id == m.thread_id)).first()
    if t is not None:
        t.status = "applied"
        record_event(
            s,
            actor=actor,
            action="setup.tab_fix_applied",
            object_type="chat_thread",
            object_id=t.id,
            after={"lines": (m.proposal or {}).get("lines")},
        )
