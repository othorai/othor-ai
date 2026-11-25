# SPDX-License-Identifier: Apache-2.0
"""The optional prose rewrite of a brief issue (docs/04 §9, docs/06), after seal, outside the run.

The model gets the template sentences (title, line, situation) and the items' titles and
consequences, all computed, and returns smoother words. v3 (0.37, #122): every number reaches the
model as a slot ({n1}) and code fills the slots in; a field with a digit or a number in words
outside a slot keeps the template ("failed:slots"), and an action may use only its own slots.
Second guard: every numeral in the filled output must appear in the input; otherwise the template
stays, and the call's guard result says why. A field or action whose direction contradicts the
items' findings ("South grew" when it fell) keeps its template (#120, `guards.contradiction`, on
the filled words; `meaning_guard` says which). The
provider off (template mode) or down: the template stays. Stored on the issue as
`prose = {source, template, model, model_id, prompt, guard}`; the page shows the model's words
only when `source == "model"`. The run's numbers never depend on this.
"""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.gateway import ModelGateway, input_hash
from othor.ai.guards import Computed, contradiction, numbers_grounded, supported
from othor.ai.models import ModelCall
from othor.ai.slots import Slots, clean_field, used
from othor.briefs.models import BriefIssue
from othor.briefs.words import FIELDS, shown

# v2 (0.33, What to do #110): the actions' sentences too, each checked on its own; v3 (0.37,
# #122): numbers as slots. v2 stays for the provenance of issues worded before
PROMPT, VERSION = "brief_rewrite", "v3"
__all__ = ["FIELDS", "rewrite_run", "shown"]
# the findings whose direction is their number's own movement (#120); a concentration, a
# spread or a broken link says "up" or "down" about something else
MOVES = ("changepoint", "net_hiding_gross")


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
        "actions": [str(a.get("text") or "") for a in body.get("actions") or []],
    }


def slotted(inputs: dict[str, Any]) -> tuple[dict[str, Any], Slots]:
    """The input with every number as a slot (one value, one slot), and the slot table."""
    slots = Slots()
    sent = slots.walk(inputs)
    return {**sent, "slots": dict(slots.values)}, slots


def _actions(got: Any, templates: list[str], slots: Slots, allowed: str) -> tuple[list[str], str]:
    """The model's action sentences, each kept only when it uses its own template's slots and no
    figure outside one (rule 1), and its filled numbers are its template's; a dropped one keeps
    its template. Returns (sentences, guard)."""
    if not templates:
        return [], "passed"
    if not isinstance(got, list) or len(got) != len(templates):
        return list(templates), f"dropped:{len(templates)}"
    out, dropped = [], 0
    for said, template in zip(got, templates, strict=True):
        own = used(slots.text(template))
        filled = clean_field(str(said or "").strip(), slots, allowed=allowed, only=own)
        if filled and numbers_grounded(filled, {"template": template}):
            out.append(filled)
        else:
            out.append(template)
            dropped += 1
    return out, "passed" if not dropped else f"dropped:{dropped}"


def result_of(s: Session, run_id: uuid.UUID, body: dict[str, Any]) -> Computed:
    """#120: what the brief's sentences were written from, by code: each item's finding that
    measures a movement (a shift in level, a net change hiding its parts) gives its number, or
    the part it is about, the finding's direction; a net change's parts their own signs, and
    the parts offsetting each other. The template's own words support an "offset" or "unchanged"
    claim. Names and computed results only."""
    from othor.engine.models import Finding, Run
    from othor.ontology.models import Spec

    run = s.scalars(sa.select(Run).where(Run.id == run_id)).first()
    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).first() if run else None
    names = {
        k: str(m.get("name") or k)
        for k, m in ((spec.compiled if spec else {}) or {}).get("metrics", {}).items()
    }
    prints = [str(i.get("fingerprint") or "") for i in body.get("items") or []]
    found = {
        f.fingerprint: f.body or {}
        for f in s.scalars(
            sa.select(Finding).where(Finding.run_id == run_id, Finding.fingerprint.in_(prints))
        )
    }
    signs: list[tuple[str, int]] = []
    offset = False
    for fp in prints:
        f = found.get(fp) or {}
        sign = {"up": 1, "down": -1}.get(str(f.get("direction")))
        if f.get("detector") not in MOVES or sign is None:
            continue
        name = names.get(str(f.get("metric")), "")
        signs.append((str(f.get("breakdown_value") or name), sign))
        if f.get("detector") == "net_hiding_gross":
            offset = True
            for part in f.get("evidence") or []:
                change = Decimal(str(part.get("change") or 0))
                if part.get("breakdown_value") and change:
                    signs.append((str(part["breakdown_value"]), 1 if change > 0 else -1))
    text = " ".join(
        [str(body.get(k) or "") for k in FIELDS]
        + [str(i.get("title") or "") for i in body.get("items") or []]
    )
    steady, offsets = supported(text)
    return Computed(signs=tuple(signs), steady=steady, offset=offset or offsets)


def _meaning(
    out: dict[str, Any], template: dict[str, Any], actions: list[str], result: Computed | None
) -> tuple[list[str], list[str]]:
    """#120: a field, or an action, with a sentence whose direction contradicts the items keeps
    its template. Returns (what was put back, why)."""
    if result is None:
        return [], []
    back: list[str] = []
    why: set[str] = set()

    def wrong(text: str) -> str | None:
        return next(filter(None, (contradiction(x, result) for x in _sentences(text))), None)

    for k in FIELDS:
        if (w := wrong(out[k])) is not None:
            out[k] = template[k]
            back.append(k)
            why.add(w)
    for i, (said, own) in enumerate(zip(out.get("actions") or [], actions, strict=False)):
        if said != own and (w := wrong(said)) is not None:
            out["actions"][i] = own
            back.append(f"action{i + 1}")
            why.add(w)
    return back, sorted(why)


def _sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?])\s+", str(text)) if x.strip()]


def rewrite(
    gateway: ModelGateway,
    company_id: uuid.UUID,
    body: dict[str, Any],
    result: Computed | None = None,
) -> dict[str, Any]:
    """result: what the items computed (`result_of`); without it, no direction check."""
    template = {k: body.get(k, "") for k in FIELDS}
    if body.get("quiet") or not body.get("title"):
        return {"source": "template", "template": template, "guard": "not-needed"}
    inputs = inputs_of(body)
    sent, slots = slotted(inputs)
    got = gateway.complete(company_id, PROMPT, VERSION, sent)
    if not got.ok or not isinstance(got.output, dict):
        return {"source": "template", "template": template, "guard": got.guard}
    allowed = json.dumps({k: v for k, v in sent.items() if k != "slots"}, ensure_ascii=False)
    filled = {
        k: clean_field(str(got.output.get(k) or slots.text(template[k])), slots, allowed=allowed)
        for k in FIELDS
    }
    if any(v is None for v in filled.values()):
        _mark(gateway, company_id, sent, "failed:slots")
        return {"source": "template", "template": template, "guard": "failed:slots"}
    out: dict[str, Any] = {k: str(v) for k, v in filled.items()}
    if not all(numbers_grounded(out[k], inputs) for k in FIELDS):
        _mark(gateway, company_id, sent, "failed:numbers")
        return {"source": "template", "template": template, "guard": "failed:numbers"}
    prose: dict[str, Any] = {
        "source": "model",
        "template": template,
        "model": out,
        "prompt": f"{PROMPT}@{VERSION}",
        "guard": "passed",
    }
    if inputs["actions"]:  # only then, so a brief without actions keeps its prose bytes
        out["actions"], prose["actions_guard"] = _actions(
            got.output.get("actions"), inputs["actions"], slots, allowed
        )
    back, why = _meaning(out, template, inputs["actions"], result)
    if back:  # only then, so a brief the check passes keeps its prose bytes
        prose["meaning_guard"] = "dropped:" + ",".join(back)
        _mark(gateway, company_id, sent, "dropped:" + ",".join(why))
    return prose


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
        prose = rewrite(gateway, company_id, issue.body, result_of(s, run_id, issue.body))
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
