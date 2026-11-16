# SPDX-License-Identifier: Apache-2.0
"""Business (board 2d): the owner's one message, a draft from their files (with the model only),
and "Where does your data live?" with the places seen in their files (by code).

A draft paragraph with a figure that isn't in the files is dropped (non-negotiable 1)."""

from __future__ import annotations

import re
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.guards import numbers_grounded
from othor.core.events import record_event
from othor.setup.drafts import refs_input
from othor.setup.models import BusinessMessage, Upload
from othor.setup.parse import Ref

PROMPT = ("business_draft", "v1")
TOPICS = ("unusual weeks", "inputs and currencies", "regulators")
_PLACES = (
    (re.compile(r"\bpostgres(ql)?\b|\bsales_dw\b|\bwarehouse\b", re.I), "PostgreSQL"),
    (re.compile(r"\bmysql\b", re.I), "MySQL"),
    (re.compile(r"\bgoogle sheets?\b|\bgsheets?\b", re.I), "Google Sheets"),
)


def message(s: Session) -> BusinessMessage:
    row = s.scalars(sa.select(BusinessMessage).order_by(BusinessMessage.version.desc())).first()
    if row is None:
        row = BusinessMessage(text="")
        s.add(row)
        s.flush()
    return row


def save(s: Session, *, text: str, data_lives: list[str], actor: str) -> BusinessMessage:
    row = message(s)
    before = {"text": row.text, "data_lives": row.data_lives}
    if (row.text or "") != text.strip() or (row.data_lives or []) != data_lives:
        row.text, row.data_lives = text.strip()[:8000], data_lives[:20]
        record_event(
            s,
            actor=actor,
            action="business.saved",
            object_type="business_message",
            object_id=row.id,
            before=before,
            after={"chars": len(row.text), "data_lives": row.data_lives},
        )
    return row


def seen_in_files(refs: list[tuple[str, Ref]]) -> list[str]:
    """Database and sheet names, and kinds of system, named in the uploads."""
    names: list[str] = []
    for _, r in refs:
        if r.kind == "table" and r.data.get("schema"):
            names.append(str(r.data["schema"]))
        if r.kind == "column" and r.data.get("tab"):
            continue
        for pat, label in _PLACES:
            if pat.search(r.text):
                names.append(label)
        for m in re.finditer(r"\b([a-z]+_(?:db|dw))\b", r.text):
            names.append(m.group(1))
        if re.search(r"order tracker", r.text, re.I):
            names.append("Order tracker")
    out: list[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out[:6]


def draft(
    gateway: Any, company_id: uuid.UUID, company_name: str, refs: list[tuple[str, Ref]]
) -> tuple[list[str], list[str]] | None:
    """(paragraphs, not covered), or None without the model."""
    if gateway is None or not refs:
        return None
    inputs = {"company": company_name, "files": refs_input(refs)}
    res = gateway.complete(company_id, *PROMPT, inputs)
    if not res.ok or res.output is None:
        return None
    paras = [
        p.strip()
        for p in res.output.get("paragraphs", [])[:6]
        if p.strip() and numbers_grounded(p, inputs)
    ]
    missing = [
        t
        for t in res.output.get("not_covered", [])
        if isinstance(t, str) and not re.search(r"\d", t)
    ][:6]
    return paras, missing


def files_read(s: Session) -> int:
    return int(
        s.scalar(
            sa.select(sa.func.count())
            .select_from(Upload)
            .where(Upload.deleted_at.is_(None), Upload.status.in_(("read", "read_as_text")))
        )
        or 0
    )
