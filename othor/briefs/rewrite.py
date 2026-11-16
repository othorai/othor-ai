# SPDX-License-Identifier: Apache-2.0
"""The optional prose rewrite of a brief issue (docs/04 §9, docs/06), after seal, outside the run.

The model gets the template sentences (title, line, situation) and the items' titles and
consequences, all computed, and returns smoother words. Guard: every numeral in the output must
appear in the input; otherwise the template stays, and the call's guard result says why. The
provider off (template mode) or down: the template stays. Stored on the issue as
`prose = {source, template, model, model_id, prompt, guard}`; the page shows the model's words
only when `source == "model"`. The run's numbers never depend on this.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.gateway import ModelGateway, input_hash
from othor.ai.guards import numbers_grounded
from othor.ai.models import ModelCall
from othor.briefs.models import BriefIssue
from othor.briefs.words import FIELDS, shown

PROMPT, VERSION = "brief_rewrite", "v1"
__all__ = ["FIELDS", "rewrite_run", "shown"]


def inputs_of(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": body.get("question", ""),
        "title": body.get("title", ""),
        "line": body.get("line", ""),
        "situation": body.get("situation", ""),
        "items": [
            {"title": i.get("title", ""), "consequence": (i.get("consequence") or {}).get("text")}
            for i in body.get("items") or []
        ],
    }


def rewrite(gateway: ModelGateway, company_id: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
    template = {k: body.get(k, "") for k in FIELDS}
    if body.get("quiet") or not body.get("title"):
        return {"source": "template", "template": template, "guard": "not-needed"}
    inputs = inputs_of(body)
    got = gateway.complete(company_id, PROMPT, VERSION, inputs)
    if not got.ok or not isinstance(got.output, dict):
        return {"source": "template", "template": template, "guard": got.guard}
    out = {k: str(got.output.get(k) or template[k]) for k in FIELDS}
    if not all(numbers_grounded(out[k], inputs) for k in FIELDS):
        _mark(gateway, company_id, inputs, "failed:numbers")
        return {"source": "template", "template": template, "guard": "failed:numbers"}
    return {
        "source": "model",
        "template": template,
        "model": out,
        "prompt": f"{PROMPT}@{VERSION}",
        "guard": "passed",
    }


def _mark(gateway: ModelGateway, company_id: uuid.UUID, inputs: dict[str, Any], guard: str) -> None:
    """The call's log row says the numbers guard refused its output (docs/06)."""
    from othor.core.tenancy import tenant_scope

    h = input_hash(PROMPT, VERSION, inputs, [[], []])
    with gateway.sf() as s, tenant_scope(company_id):
        row = s.scalars(
            sa.select(ModelCall)
            .where(ModelCall.prompt_id == PROMPT, ModelCall.input_hash == h)
            .order_by(ModelCall.created_at.desc())
            .limit(1)
        ).first()
        if row is not None:
            row.guard_result = guard
            s.commit()


def rewrite_run(s: Session, gateway: ModelGateway, company_id: uuid.UUID, run_id: uuid.UUID) -> int:
    """Every issue of a sealed run; returns how many got the model's words."""
    from othor.core.events import SYSTEM_ACTOR, record_event

    n = 0
    for issue in s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id == run_id)):
        prose = rewrite(gateway, company_id, issue.body)
        issue.prose = prose
        n += prose["source"] == "model"
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="brief.prose",
            object_type="brief_issue",
            object_id=issue.id,
            after={"source": prose["source"], "guard": prose.get("guard", "")},
        )
    return n
