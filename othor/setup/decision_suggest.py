# SPDX-License-Identifier: Apache-2.0
"""Chat 1.3 (owner, 20 Nov 2026): every setup decision opens with Othor AI's suggested answer
already chosen and why in one line, with Apply and Suggest edits, instead of empty choices.

After the values are computed (the draft job), one model call per open decision (`decision_suggest`
v1) reads the decision's names and computed values, the owner's business message, the uploaded
files' names and the other numbers' definitions (names and computed results only, never rows:
rule 3) and returns a choice id and why. Code checks it: the id must be one of the decision's own
choices, and a `why` with a figure in it is discarded with its pick (rule 1: the values beside the
choices are computed). A decision Othor AI can't call stays open with no pick; without a model
nothing is stored and the panel asks as before. The owner's Apply is the decision (setup decisions
are the owner's: no "Drafts only" note, rule 6).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.setup import drafts
from othor.setup.models import SetupDraft

PROMPT, VERSION = "decision_suggest", "v1"
WHY_MAX = 200
_DIGIT = re.compile(r"\d")


def choices(r: SetupDraft) -> list[dict[str, str]] | None:
    """The decision's choices (id, label, where, value) or None when it isn't one to pick:
    an open conflict's two definitions and "both", or a redrafted definition's accept or keep.
    Values are the computed ones, as text."""
    from othor.web.setup_view import value_text

    b = r.body or {}
    if r.changed:
        return [
            {
                "id": "accept",
                "label": "Use the redrafted one",
                "where": str(b.get("description") or ""),
            },
            {
                "id": "reject",
                "label": "Keep mine",
                "where": str((r.prev_body or {}).get("description") or ""),
            },
        ]
    c = (r.value or {}).get("conflict") if r.confidence == "conflict" else None
    if not c:
        return None
    unit = b.get("unit", "inr")
    out = [
        {
            "id": str(o["id"]),
            "label": str(o["label"]),
            "where": str(o["where"]),
            "value": f"{c['month']} · {value_text(o['value'], unit)}",
        }
        for o in c["options"]
    ]
    out.append({"id": "both", "label": "Show both in the brief", "where": "Each with its label"})
    return out


def _question(r: SetupDraft) -> str:
    if r.changed:
        return "Use the redrafted definition?"
    c = (r.value or {}).get("conflict") or {}
    return str(c.get("question") or "")


def inputs(s: Session, r: SetupDraft) -> dict[str, Any] | None:
    """The model's input for one decision: names, the question and the computed values."""
    from othor.setup import business
    from othor.setup.models import Upload

    opts = choices(r)
    if not opts:
        return None
    others = [
        {"name": str(x.body.get("name") or x.key), "definition": str(x.body.get("description"))}
        for x in drafts.stored(s)
        if x.key != r.key and x.state != "skipped" and x.body.get("description")
    ][:30]
    files = sorted(
        {u.filename for u in s.scalars(sa.select(Upload).where(Upload.deleted_at.is_(None)))}
    )
    return {
        "metric": str(r.body.get("name") or r.key),
        "question": _question(r),
        "choices": opts,
        "business": (business.message(s).text or "")[:4000],
        "files": files[:20],
        "numbers": others,
    }


def check(out: Any, opts: list[dict[str, str]]) -> dict[str, str]:
    """The model's answer, checked by code: an id of its own choices and a why with no figure.
    {"choice": ""} when it can't call it (stored, so the panel asks without a pick)."""
    if not isinstance(out, dict):
        return {"choice": "", "why": ""}
    choice = str(out.get("choice") or "")
    why = " ".join(str(out.get("why") or "").split())[:WHY_MAX]
    if choice not in {o["id"] for o in opts} or not why or _DIGIT.search(why):
        return {"choice": "", "why": ""}
    return {"choice": choice, "why": why}


def suggest_all(sf: Any, gateway: Any, company_id: uuid.UUID) -> int:
    """One suggestion per open decision without one yet (or whose inputs changed: the gateway
    answers a repeat from its cache). The gateway logs each call in its own session, so the
    inputs are gathered first and the answers stored after. Returns how many were stored."""
    from othor.core.tenancy import Repo, tenant_scope

    with sf() as s, tenant_scope(company_id):
        asks = []
        for r in drafts.stored(s):
            if r.state == "skipped":
                continue
            inp = inputs(s, r)
            if inp is not None:
                asks.append((r.id, inp))
    got: list[tuple[Any, dict[str, str]]] = []
    for rid, inp in asks:
        res = gateway.complete(company_id, PROMPT, VERSION, inp)
        if not res.ok:
            continue  # no model, capped or unavailable: no pick, as before (rule 4)
        got.append((rid, check(res.output, inp["choices"])))
    if not got:
        return 0
    with sf() as s, tenant_scope(company_id):
        for rid, sug in got:
            row = Repo(s).get(SetupDraft, rid)
            if row is None:
                continue
            row.value = {**(row.value or {}), "suggest": {**sug, "draft": row.draft_no}}
        s.commit()
    return len(got)


def pick(r: SetupDraft) -> dict[str, str] | None:
    """The stored suggestion, when it still fits this decision (its choices and draft)."""
    sug = (r.value or {}).get("suggest") or {}
    opts = choices(r)
    if not opts or not sug.get("choice") or sug.get("draft") != r.draft_no:
        return None
    opt = next((o for o in opts if o["id"] == sug["choice"]), None)
    if opt is None:
        return None
    return {**opt, "why": str(sug.get("why") or "")}
