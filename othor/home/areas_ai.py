# SPDX-License-Identifier: Apache-2.0
"""Othor AI drafts the areas (M16.1, `metric_areas` v1): after seal, outside the run, the model
reads the listed numbers' keys, names, descriptions, units and families (no values, no rows) and
puts each under one area from the short list. The draft arrives on Home as one proposal, "Group
your numbers into areas"; nothing changes until the owner accepts it. The model off or down:
no proposal, and Summary shows "Other numbers".
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.guards import numerals
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.engine.models import Proposal
from othor.ontology import areas, service

PROMPT = ("metric_areas", "v1")
KIND = "areas"
TEXT = "Group your numbers into areas"


def waiting(s: Session) -> dict[str, dict[str, Any]]:
    """The listed numbers with no area yet (confirmed, or waiting for a source), by key."""
    return {
        r.key: dict(r.body or {})
        for r in service.active(s, ["metric"])
        if (r.body or {}).get("listed", True)
        and (r.body or {}).get("status", "active") in ("active", "needs_source")
        and not (r.body or {}).get("area")
    }


def inputs_of(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "numbers": [
            {
                "key": k,
                "name": str(b.get("name") or k),
                "description": str(b.get("description") or ""),
                "unit": str(b.get("unit") or ""),
                "family": str(b.get("family") or ""),
            }
            for k, b in sorted(metrics.items())
        ]
    }


def clean(out: Any, keys: set[str]) -> dict[str, str]:
    """The model's areas for known keys, from the short list only; "Other" is no area."""
    got: dict[str, str] = {}
    items = (out or {}).get("areas") if isinstance(out, dict) else None
    for r in items or []:
        if not isinstance(r, dict):
            continue
        key, raw = str(r.get("key") or ""), str(r.get("area") or "")
        if key not in keys or raw not in areas.MODEL_CHOICES or numerals(raw):
            continue
        a = areas.clean(raw)
        if a is not None:
            got[key] = a
    return got


def _asked_before(s: Session) -> set[str]:
    """Every key an earlier areas proposal asked about, whatever the owner decided: a decline,
    or a number the model left in Other, is never asked again for the same numbers."""
    out: set[str] = set()
    for ev in s.scalars(sa.select(Proposal.evidence).where(Proposal.kind == KIND)):
        out |= set((ev or {}).get("asked") or [])
    return out


def line(found: dict[str, str], names: dict[str, str]) -> str:
    by: dict[str, list[str]] = {}
    for key, a in found.items():
        by.setdefault(a, []).append(names.get(key, key))
    order = areas.in_use({"area": a} for a in found.values())
    return " · ".join(f"{a}: {', '.join(sorted(by[a]))}" for a in order if a in by)


def propose_areas(s: Session, gateway: Any, company_id: uuid.UUID) -> Proposal | None:
    """One proposal for the numbers with no area, inside the company's tenant scope; None when
    every number has one, it was asked before, one is already open, or the model gave nothing."""
    todo = waiting(s)
    if not todo:
        return None
    if s.scalars(
        sa.select(Proposal.id).where(Proposal.kind == KIND, Proposal.status == "open")
    ).first():
        return None
    if set(todo) <= _asked_before(s):
        return None
    res = gateway.complete(company_id, *PROMPT, inputs_of(todo))
    found = clean(res.output if res.ok else None, set(todo))
    if not found:
        return None
    asked = sorted(todo)
    fp = "areas:" + hashlib.sha256(",".join(asked).encode()).hexdigest()[:32]  # fits 64
    names = {k: str(b.get("name") or k) for k, b in todo.items()}
    p = Proposal(
        kind=KIND,
        text=TEXT,
        evidence={
            "areas": dict(sorted(found.items())),
            "asked": asked,
            "line": line(found, names),
            "confidence": "medium",
            "prompt": "@".join(PROMPT),
        },
        origin={"rule": "metric_areas"},
        fingerprint=fp,
        status="open",
        run_id=None,
    )
    s.add(p)
    s.flush()
    record_event(
        s,
        actor=SYSTEM_ACTOR,
        action="proposal.created",
        object_type="proposal",
        object_id=p.id,
        after={"kind": KIND, "fingerprint": fp, "prompt": "@".join(PROMPT)},
    )
    return p
