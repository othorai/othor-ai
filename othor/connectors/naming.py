# SPDX-License-Identifier: Apache-2.0
"""A source's plain name (0.26, M26.3; owner 18 Nov 2026): "Sales records (sales_dw)" reads
better than "sales_dw" in the chat's source tags and on Data sources.

The suggestion is built by code from what Othor AI already wrote about the source's tables at
the scan (each table's "looks like" label, "Invoices · amount, customer, date"): the "what it is"
part of its two largest tables that numbers come from (a table without a label: its name in
words, as the scan's rules say it). No new model call; with no labels, the connector's name. The
owner saves it (or types their own); nothing is renamed until they do (non-negotiable 6)."""

from __future__ import annotations

import re
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.connectors.models import Source, SourceTable
from othor.core.events import record_event

MAX = 80
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def plain(src: Source) -> str:
    return src.display_name or src.name


def suggest(s: Session, src: Source) -> str:
    rows = s.scalars(
        sa.select(SourceTable)
        .where(
            SourceTable.source_id == src.id,
            SourceTable.use.in_(("metric_source", "dimension")),
        )
        .order_by(
            (SourceTable.use != "metric_source"),  # the tables numbers come from, first
            SourceTable.row_count.desc().nulls_last(),
            SourceTable.name,
        )
    ).all()
    whats: list[str] = []
    for t in rows:
        what = str(t.looks_like or "").split("·", 1)[0].strip() or _noun(t.name)
        if what and what.lower() not in {w.lower() for w in whats}:
            whats.append(what)
        if len(whats) == 2:
            break
    if not whats:
        return src.name
    name = whats[0] if len(whats) == 1 else f"{whats[0]} and {whats[1][:1].lower()}{whats[1][1:]}"
    return name[:MAX]


def _noun(table: str) -> str:
    """What the scan's rules call a table without its label ("fact_invoice" → "Invoice")."""
    from othor.connectors.classify import _STRIP_PREFIX, _words, sentence

    return sentence(_words(_STRIP_PREFIX.sub("", table.lower())))


def clean(text: str) -> str:
    return " ".join(_CTRL.sub(" ", text or "").split())[:MAX]


def rename(s: Session, src: Source, text: str, *, actor: str) -> dict[str, Any]:
    """Save the plain name ("" clears it: the connector's name shows again). One event."""
    new = clean(text) or None
    before = src.display_name
    if new == before:
        return {"changed": False}
    src.display_name = new
    record_event(
        s,
        actor=actor,
        action="source.renamed",
        object_type="source",
        object_id=src.id,
        before={"display_name": before},
        after={"display_name": new},
    )
    return {"changed": True}
