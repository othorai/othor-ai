# SPDX-License-Identifier: Apache-2.0
"""The chat engine for fixing a metric (M4b: 2nb Where is it?, 2nc Connect, 2nd Edit, 6ha Fix).

A chat is a thread on one draft. Each owner turn is a quick reply (a typed patch, an action) or
typed text. The worker turns it into a reply and a draft card:
- a quick reply's patch, or the patch Claude proposes for typed text (typed, never SQL);
- applied to a copy of the definition, validated, compiled and its value computed by SQL;
- shown line by line with today's value and the value before.
Apply writes the patched definition (and any rules it needs) to the setup draft. Without the
model, typed text gets "Not available right now"; quick replies and Apply work the same.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.guards import numbers_grounded
from othor.core.events import record_event
from othor.ontology.compiler.catalog import Catalog
from othor.ontology.types import validate_body
from othor.setup import drafts
from othor.setup.candidates import (
    DURATION,
    column_candidates,
    duration_candidates,
    table_candidates,
)
from othor.setup.models import ChatMessage, ChatThread, SetupDraft
from othor.setup.patches import Patch, _breakdown_column, apply_patch

PURPOSES = {
    "where": "metric_where",
    "connect": "metric_connect",
    "edit": "metric_edit",
    "fix": "metric_edit",
}
NOT_AVAILABLE = "Not available right now"


@dataclass
class QuickReply:
    label: str
    patch: Patch | None = None
    action: str | None = None  # skip · needs_source · form · connect:<kind> · ask
    note: str | None = None  # a candidate's details, shown under it
    inputs: list[dict[str, Any]] = field(default_factory=list)  # a small form: name, label, options

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "patch": self.patch.as_json() if self.patch else None,
            "action": self.action,
            "note": self.note,
            "inputs": self.inputs,
        }


def thread_for(s: Session, key: str, purpose: str, *, create: bool = True) -> ChatThread | None:
    subject = f"metric:{key}"
    t = s.scalars(
        sa.select(ChatThread).where(
            ChatThread.subject == subject,
            ChatThread.purpose == purpose,
            ChatThread.status == "open",
        )
    ).first()
    if t is None and create:
        t = ChatThread(purpose=purpose, subject=subject)
        s.add(t)
        s.flush()
    return t


def messages(s: Session, thread: ChatThread | None) -> list[ChatMessage]:
    if thread is None:
        return []
    return list(
        s.scalars(
            sa.select(ChatMessage)
            .where(ChatMessage.thread_id == thread.id)
            .order_by(ChatMessage.created_at)
        )
    )


# --- quick replies (code, always) -----------------------------------------------------------


def _first_date(t: Any) -> str | None:
    cols = [
        c.name for c in t.columns.values() if c.kind in ("date", "timestamp") and not c.personal
    ]
    cols.sort(key=lambda n: (not n.lower().endswith("_date"), n))
    return cols[0] if cols else None


def _has_threshold(body: dict[str, Any]) -> int | None:
    from othor.setup.patches import is_threshold

    for f in body.get("filters", []):
        if is_threshold(f):
            return int(f["args"][1]["lit"])
    return None


def _date_cols_in_measure(body: dict[str, Any]) -> list[str]:
    expr = (body.get("measure") or {}).get("expr") or {}
    if expr.get("fn") != "days_between":
        return []
    return [a["col"] for a in expr.get("args", []) if isinstance(a, dict) and "col" in a]


def quick_replies(
    row: SetupDraft,
    purpose: str,
    cat: Catalog,
    records: dict[tuple[str, str], Any],
    suggested: list[Any] | None = None,
) -> list[QuickReply]:
    b = row.body
    t = b.get("table") or {}
    near = (t.get("source", ""), t.get("schema", ""), t.get("table", "")) if t else None
    words = " ".join(
        x
        for x in (b.get("name", ""), b.get("description", ""), b.get("needs", ""), row.note or "")
        if x
    )
    out: list[QuickReply] = []
    if purpose == "where":
        if DURATION.search(str(b.get("name") or row.key)):
            for dc in duration_candidates(cat, words):
                out.append(
                    QuickReply(
                        f"Days from {dc.start} to {dc.end}",
                        Patch(
                            "use_duration",
                            {
                                "table": {
                                    "source": dc.table.source_name,
                                    "schema": dc.table.schema,
                                    "table": dc.table.name,
                                },
                                "start": dc.start,
                                "end": dc.end,
                            },
                        ),
                        note=f"{dc.table.schema}.{dc.table.name} · {dc.rows:,} rows",
                    )
                )
        cands = list(column_candidates(cat, words, near=near))
        # the same column name in several tables: say which table (2nb, M12b)
        names = [c.column for c in cands]
        for c in cands:
            date = _first_date(c.table)
            label = (
                f"Use {c.table.name}.{c.column}" if names.count(c.column) > 1 else f"Use {c.column}"
            )
            note = " · ".join(
                p
                for p in (
                    c.unit,
                    f"{c.rows:,} rows" if c.rows else "",
                    f"joins on {', '.join(c.joins)}" if c.joins else "",
                )
                if p
            )
            out.append(
                QuickReply(
                    label,
                    Patch(
                        "use_column",
                        {
                            "table": {
                                "source": c.table.source_name,
                                "schema": c.table.schema,
                                "table": c.table.name,
                            },
                            "column": c.column,
                            "date": date,
                        },
                    ),
                    note=f"{c.table.schema}.{c.table.name}.{c.column} · {note}",
                )
            )
        out.append(QuickReply("It’s somewhere else", action="ask"))
        out.append(QuickReply("We don’t track this", action="needs_source"))
    elif purpose == "connect":
        for sug in (suggested or [])[:1]:
            out.append(
                QuickReply(
                    f"Connect the {sug.name or sug.title}",
                    action=f"connect:{sug.kind}",
                    note=sug.text,
                )
            )
        for tc in table_candidates(cat, words):
            amount = next((u for u in tc.useful if re.search("amount|value", u)), None)
            if not amount:
                continue
            out.append(
                QuickReply(
                    f"Use {tc.table.name}",
                    Patch(
                        "use_column",
                        {
                            "table": {
                                "source": tc.table.source_name,
                                "schema": tc.table.schema,
                                "table": tc.table.name,
                            },
                            "column": amount,
                            "date": _first_date(tc.table),
                        },
                    ),
                    note=f"{tc.table.source_name} · {tc.rows:,} rows · {', '.join(tc.useful[:4])}",
                )
            )
        out.append(QuickReply("Skip for now", action="skip"))
    else:  # edit, fix
        th = _has_threshold(b)
        if th is not None:
            out.append(
                QuickReply(
                    "Change the threshold",
                    action="form",
                    inputs=[
                        {"name": "value", "label": "Days", "value": th, "patch": "set_threshold"}
                    ],
                )
            )
        tinfo = cat.tables.get(near) if near else None
        bds = sorted(
            k
            for (kind, k), bd in records.items()
            if kind == "breakdown"
            and tinfo is not None
            and _breakdown_column(bd, tinfo, records) is not None
        )
        if b.get("table") and bds:
            out.append(
                QuickReply(
                    "Exclude some values",
                    action="form",
                    inputs=[
                        {"name": "breakdown", "label": "Of", "options": bds, "patch": "exclude"},
                        {"name": "values", "label": "Values", "value": ""},
                    ],
                )
            )
            out.append(
                QuickReply(
                    "Break down by",
                    action="form",
                    inputs=[
                        {
                            "name": "breakdowns",
                            "label": "Breakdowns",
                            "options": bds,
                            "multiple": True,
                            "patch": "set_breakdowns",
                        }
                    ],
                )
            )
        dates = _date_cols_in_measure(b)
        if dates and tinfo is not None:
            others = [
                c.name
                for c in tinfo.columns.values()
                if c.kind in ("date", "timestamp") and not c.personal and c.name not in dates
            ]
            for d in dates[:2]:
                for o in others[:2]:
                    out.append(
                        QuickReply(
                            f"Use {o} when {d} is missing",
                            Patch("coalesce_date", {"primary": d, "fallback": o}),
                        )
                    )
        out.append(QuickReply("Edit by form", action="form_panel"))
    return out


# --- turns ------------------------------------------------------------------------------------


def add_turn(
    s: Session, thread: ChatThread, *, text: str, patch: Patch | None, actor: str
) -> ChatMessage:
    """The owner's turn; the worker answers it (job 'chat')."""
    m = ChatMessage(
        thread_id=thread.id,
        role="user",
        text=text[:2000],
        proposal={"patch": patch.as_json(), "state": "pending"} if patch else {"state": "pending"},
    )
    s.add(m)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="setup.chat_turn",
        object_type="chat_thread",
        object_id=thread.id,
        after={"purpose": thread.purpose, "patch": patch.kind if patch else None},
    )
    return m


def model_inputs(
    row: SetupDraft,
    purpose: str,
    cat: Catalog,
    history: list[ChatMessage],
    replies: list[QuickReply],
) -> dict[str, Any]:
    from othor.setup.drafts import catalog_input

    return {
        "purpose": purpose,
        "metric": {
            "key": row.key,
            "name": row.body.get("name"),
            "definition": row.body,
            "question": row.note,
            "today": (row.value or {}).get("value"),
        },
        "tables": catalog_input(cat),
        "options": [q.as_json() for q in replies if q.patch],
        "conversation": [{"role": m.role, "text": m.text} for m in history[-10:]],
    }


def proposal_from_model(out: dict[str, Any], inputs: Any = None) -> Patch | None:
    """The model's typed patch. With `inputs`, a patch carrying a number that isn't in them (a
    threshold, a factor the owner never gave) is dropped: no number comes from a model."""
    p = out.get("patch") or {}
    if not p.get("kind"):
        return None

    def empty(v: Any) -> bool:
        if isinstance(v, dict):
            return all(empty(x) for x in v.values())
        return v in (None, "", [])

    args = {k: v for k, v in (p.get("args") or {}).items() if not empty(v)}
    if inputs is not None and not numbers_grounded(
        json.dumps(args, ensure_ascii=False, default=str), inputs
    ):
        return None
    return Patch(p["kind"], args)


def reply_text(out: dict[str, Any], inputs: dict[str, Any]) -> str:
    text = str(out.get("text") or "").strip()
    if not text or not numbers_grounded(text, inputs):
        return "Here is the change and what it does today."
    return text[:1200]


@dataclass
class Card:
    body: dict[str, Any]
    records: list[tuple[str, str, dict[str, Any]]]
    lines: list[str]

    def as_json(self) -> dict[str, Any]:
        return {"body": self.body, "records": [list(r) for r in self.records], "lines": self.lines}


def card_for(
    row: SetupDraft, patch: Patch, cat: Catalog, records: dict[tuple[str, str], Any]
) -> Card:
    """Raises PatchRefused with a reason the owner can read."""
    got = apply_patch(row.body, patch, cat, records)
    return Card(got.body, got.records, got.lines)


def apply_card(s: Session, row: SetupDraft, card: dict[str, Any], *, actor: str) -> None:
    """Apply: the draft takes the patched definition; rules it needs become setup records."""
    before = {"body": row.body}
    row.body = validate_body("metric", {**card["body"], "confidence": "high"})
    row.confidence, row.note = "high", None
    if row.state in ("needs_source", "excluded_table"):
        row.state = "needs_confirmation"
    row.value = None
    for type_, key, body in card.get("records", []):
        existing = s.scalars(
            sa.select(SetupDraft).where(SetupDraft.kind == type_, SetupDraft.key == key)
        ).first()
        if existing is None:
            s.add(
                SetupDraft(
                    kind=type_,
                    key=key,
                    body=validate_body(type_, body),
                    confidence="high",
                    state="confirmed",
                    frozen=True,
                    origin={"by": "owner", "from": f"chat:{row.key}"},
                )
            )
        else:
            existing.body = validate_body(type_, body)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="setup.chat_applied",
        object_type="setup_draft",
        object_id=row.id,
        before=before,
        after={"body": row.body, "lines": card.get("lines")},
    )


def act(s: Session, row: SetupDraft, action: str, *, actor: str) -> None:
    """Quick-reply actions that change the row without a card."""
    before = {"state": row.state}
    if action == "skip":
        row.state = "skipped"
    elif action == "needs_source":
        row.body = validate_body(
            "metric",
            {
                "name": row.body.get("name", row.key),
                "status": "needs_source",
                "needs": "Not tracked yet",
            },
        )
        row.state, row.note = "needs_source", drafts.NO_SOURCE
    else:
        return
    record_event(
        s,
        actor=actor,
        action=f"setup.chat_{action}",
        object_type="setup_draft",
        object_id=row.id,
        before=before,
        after={"state": row.state},
    )


def find_row(s: Session, key: str) -> SetupDraft | None:
    return s.scalars(
        sa.select(SetupDraft).where(SetupDraft.kind == "metric", SetupDraft.key == key)
    ).first()
