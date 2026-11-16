# SPDX-License-Identifier: Apache-2.0
"""Othor AI's ranking of suggested pairs (v0.12, `suggest_pairs` v1, docs/06), after seal, outside
the run: the model sees metric names, units and kinds only, and the pairs the data supports by
key. It ranks; code's computed evidence still orders the cards and says why. A reason with a
number in it is dropped (the numbers guard); the model off or down leaves code's ranking.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.guards import numerals
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.ontology import suggest as sg
from othor.ontology.models import Question

PROMPT = ("suggest_pairs", "v1")
REASON_MAX = 120


def inputs_of(spec: dict[str, Any], pool: list[dict[str, Any]]) -> dict[str, Any]:
    keys = sorted({p["a"] for p in pool} | {p["b"] for p in pool})
    metrics = spec.get("metrics") or {}
    return {
        "numbers": [
            {
                "key": k,
                "name": str(metrics.get(k, {}).get("name") or k),
                "unit": str(metrics.get(k, {}).get("unit") or ""),
                "kind": str(metrics.get(k, {}).get("kind") or "flow"),
            }
            for k in keys
        ],
        "supported": [{"a": p["a"], "b": p["b"]} for p in pool],
    }


def clean(out: Any, pool: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The model's pairs that the data supports, best first, with a reason only when it holds no
    number (non-negotiable 1)."""
    ok = {frozenset((p["a"], p["b"])) for p in pool}
    got: list[dict[str, str]] = []
    for r in (out or {}).get("pairs") or [] if isinstance(out, dict) else []:
        if not isinstance(r, dict):
            continue
        a, b = str(r.get("a") or ""), str(r.get("b") or "")
        if frozenset((a, b)) not in ok:
            continue
        reason = " ".join(str(r.get("reason") or "").split())[:REASON_MAX]
        if numerals(reason):
            reason = ""
        got.append({"a": a, "b": b, "reason": reason.rstrip(".")})
    return got


def rank_pairs(s: Session, gateway: Any, company_id: uuid.UUID, spec: dict[str, Any]) -> int:
    """Re-rank every waiting pair question's cards with Othor AI; returns how many changed."""
    n = 0
    for q in s.scalars(
        sa.select(Question).where(
            Question.kind == "needs", Question.status.in_(("open", "skipped"))
        )
    ):
        sub = dict(q.subject or {})
        pool = list(sub.get("pool") or [])
        if sub.get("needs") != "pair" or not pool or q.draft:
            continue
        res = gateway.complete(company_id, *PROMPT, inputs_of(spec, pool))
        ranked = clean(res.output if res.ok else None, pool)
        if not ranked:
            continue  # code's ranking stays
        merged = sg.merge_ranked(spec, sub, ranked)
        if merged != sub:
            q.subject = merged
            n += 1
            record_event(
                s,
                actor=SYSTEM_ACTOR,
                action="question.suggestions_ranked",
                object_type="question",
                object_id=q.id,
                after={"prompt": "@".join(PROMPT), "pairs": len(ranked)},
            )
    return n
