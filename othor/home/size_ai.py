# SPDX-License-Identifier: Apache-2.0
"""Othor AI's words for the numbers suggested for the company's size (#76, `size_numbers` v1,
docs/06), after seal, outside the run, with the proposals' other post-run steps.

Othor AI words only the reason. It gets names (the numbers, what each is built from, the goal it
serves and whether that goal is behind), the band as words and what the business does; never a
figure and never a row. A reason with any number in it, in numerals or words, is dropped by the
numbers guard (non-negotiable 1); the figure beside it is always code's. One call a night at
most, only when a suggestion still has the catalogue's words; no model leaves those (rule 4).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from othor.ai.guards import numerals
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.ontology import service, size_numbers

PROMPT = ("size_numbers", "v1")
REASON_MAX = 200
ABOUT_MAX = 300


def _about(s: Session) -> str:
    for r in service.active(s, ["business_fact"]):
        if (r.body or {}).get("kind", "description") == "description":
            return str((r.body or {}).get("text") or "")[:ABOUT_MAX]
    return ""


def inputs_of(s: Session, rows: list[Any], spec_metrics: dict[str, Any]) -> dict[str, Any]:
    first = rows[0].evidence or {}
    numbers = []
    for p in rows:
        ev = p.evidence or {}
        goal = ev.get("goal") or None
        numbers.append(
            {
                "key": ev["key"],
                "name": ev["name"],
                "area": ev.get("area") or "",
                "built_from": [
                    str(spec_metrics.get(m, {}).get("name") or m)
                    for m in (ev.get("parts") or {}).values()
                ],
                "goal": (
                    {
                        "name": goal["name"],
                        "behind": goal["state"] in ("at_risk", "off_track"),
                    }
                    if goal
                    else None
                ),
                "reason_without_you": ev.get("reason") if ev.get("reason_by") == "template" else "",
            }
        )
    return {"band": first.get("band_label", ""), "business": _about(s), "numbers": numbers}


def clean(out: Any, keys: set[str]) -> dict[str, str]:
    """The model's reasons for known keys, only those with no number in them."""
    got: dict[str, str] = {}
    rows = (out or {}).get("reasons") if isinstance(out, dict) else None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        key = str(r.get("key") or "")
        reason = " ".join(str(r.get("reason") or "").split())[:REASON_MAX].rstrip(". ")
        if key not in keys or not reason or numerals(reason):
            continue
        got[key] = reason
    return got


def word_reasons(s: Session, gateway: Any, company_id: uuid.UUID, spec: dict[str, Any]) -> int:
    """Reword the open suggestions that still carry the catalogue's reason; returns how many
    changed. A suggestion asked about once keeps its words until it's proposed afresh."""
    rows = [
        p
        for p in size_numbers.open_rows(s)
        if (p.evidence or {}).get("status") == "now"
        and (p.evidence or {}).get("reason_by") == "template"
        and not (p.evidence or {}).get("worded")
    ]
    if not rows:
        return 0
    inputs = inputs_of(s, rows, spec.get("metrics") or {})
    res = gateway.complete(company_id, *PROMPT, inputs)
    if not res.ok:  # no model, or it's down: the catalogue's words stay, asked again tomorrow
        return 0
    found = clean(res.output, {(p.evidence or {})["key"] for p in rows})
    n = 0
    for p in rows:
        ev = dict(p.evidence or {})
        ev["worded"] = True  # asked once: no second call for the same suggestion
        reason = found.get(ev["key"])
        if reason:
            ev["reason"], ev["reason_by"], ev["prompt"] = reason, "othor_ai", "@".join(PROMPT)
            ev["line"] = size_numbers.compose(ev)
            n += 1
        p.evidence = ev
    if n:
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="proposal.reworded",
            object_type="proposal",
            object_id=rows[0].id,
            after={"kind": size_numbers.KIND, "prompt": "@".join(PROMPT), "count": n},
        )
    return n
