# SPDX-License-Identifier: Apache-2.0
"""Choose (2na) and Confirm (2n "Confirm and continue").

Confirm writes what setup settled as draft Ontology records: the metrics (with the owner's name
and the date), the rules each discovery proposes (unless skipped), the choices made in chats, and
the business message. Then `service.confirm` makes them all version 1, the first spec is compiled
from it, and the setup moves to the first run. Confirmed drafts are frozen: Redraft never touches
them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.events import record_event
from othor.core.models import Company, SetupState
from othor.jobs.queue import enqueue
from othor.ontology import service
from othor.ontology.compiler.api import compile_version
from othor.ontology.compiler.catalog import load_catalog
from othor.ontology.models import OntologyVersion
from othor.ontology.types import validate_body
from othor.setup import conflicts, drafts
from othor.setup.models import BusinessMessage, SetupDraft


class NotReady(ValueError):
    pass


def in_use(rows: list[SetupDraft]) -> int:
    """The setup metrics that will be computed every night once confirmed (the ceiling counts
    these, M15.1): not skipped, and not waiting for a source."""
    return sum(
        1 for r in rows if r.state != "skipped" and r.body.get("status", "active") == "active"
    )


def over_ceiling(s: Session) -> int:
    """How many setup metrics to skip before Confirm (0 when within the ceiling)."""
    return max(0, in_use(drafts.stored(s)) - service.max_metrics())


def open_conflicts(s: Session) -> list[SetupDraft]:
    return [r for r in drafts.stored(s) if r.confidence == "conflict" and r.state != "skipped"]


def drafting(s: Session) -> bool:
    meta = s.scalars(
        sa.select(SetupDraft).where(SetupDraft.kind == "meta", SetupDraft.key == "drafting")
    ).first()
    return meta is not None and meta.body.get("state") not in (None, "done")


def choose(s: Session, row: SetupDraft, choice: str, *, actor: str) -> str:
    """Settle a conflict: the metric body and the rules the choice means. Returns the sentence
    saved to the Ontology. The value is recomputed by the caller's "values" job."""
    conflict = (row.value or {}).get("conflict")
    if conflict is None or choice not in ("source", "upload", "both"):
        raise ValueError("Nothing to choose here.")
    cat = load_catalog(s)
    t = row.body.get("table") or {}
    info = cat.tables.get((t.get("source", ""), t.get("schema", ""), t.get("table", "")))
    cols = {c.lower() for c in info.columns} if info else set()
    body, records, text = conflicts.resolution(conflict, choice, row.body, cols)
    before = {"body": row.body, "confidence": row.confidence}
    # what the row was, kept with the answer so Change (the Decisions panel) can open it again
    was = {"body": row.body, "value": row.value, "note": row.note}
    row.body = validate_body("metric", body)
    row.confidence, row.note = "high", None
    row.value = {k: v for k, v in (row.value or {}).items() if k != "conflict"}
    row.value["settled"] = {"choice": choice, "text": text, "was": was}
    for type_, key, b in records:
        existing = s.scalars(
            sa.select(SetupDraft).where(SetupDraft.kind == type_, SetupDraft.key == key)
        ).first()
        body_ = validate_body(type_, b)
        if existing is None:
            s.add(
                SetupDraft(
                    kind=type_,
                    key=key,
                    body=body_,
                    confidence="high",
                    state="confirmed",
                    frozen=True,
                    origin={"by": "owner", "from": f"conflict:{row.key}"},
                )
            )
        else:
            existing.body = body_
    s.flush()
    record_event(
        s,
        actor=actor,
        action="setup.conflict_settled",
        object_type="setup_draft",
        object_id=row.id,
        before=before,
        after={"choice": choice, "text": text},
    )
    return text


def reopen(s: Session, row: SetupDraft, *, actor: str) -> bool:
    """Change on a decided item (the Decisions panel): the question opens again as it was. A
    settled conflict gets its body, value and question back and the records its answer wrote
    go; a redrafted definition is to accept or keep again; a set-aside metric waits again.
    Returns True when the values need recomputing. One event."""
    val = row.value or {}
    settled = val.get("settled") or {}
    before = {"state": row.state, "settled": settled.get("text")}
    recompute = False
    if row.state == "skipped":
        if (row.origin or {}).get("set_aside") or row.body.get("status") != "needs_source":
            return False
        row.state = str((row.origin or {}).get("skipped_from") or "needs_source")
    elif settled.get("was") and settled.get("choice") in ("source", "upload", "both"):
        was = settled["was"]
        row.body, row.value, row.note = was["body"], was["value"], was.get("note")
        row.confidence = "conflict"
        for r in s.scalars(
            sa.select(SetupDraft).where(SetupDraft.kind.in_(("rule", "vocabulary")))
        ):
            if (r.origin or {}).get("from") == f"conflict:{row.key}":
                s.delete(r)
    elif settled.get("choice") in ("accept", "reject") and settled.get("prev") is not None:
        row.body, row.prev_body, row.changed = settled["new"], settled["prev"], True
        row.value = {k: v for k, v in val.items() if k != "settled"} or None
        recompute = True
    else:
        return False
    record_event(
        s,
        actor=actor,
        action="setup.decision_reopened",
        object_type="setup_draft",
        object_id=row.id,
        before=before,
        after={"state": row.state},
    )
    return recompute


FIRST_RUN_KEY = "dry:first"


def confirm_setup(
    s: Session,
    company: Company,
    *,
    actor: str,
    today: dt.date,
    run_date: dt.date | None = None,
) -> OntologyVersion:
    """Confirm → Ontology v1, its spec, and dry run #0001 queued (2o). `run_date` is the
    company's local date (default `today`); the run sees data up to the day before."""
    if service.confirmed(s):
        raise service.AlreadyConfirmed("Setup is already confirmed.")
    if drafting(s):
        raise NotReady("Drafting is still running.")
    if over_ceiling(s):
        raise NotReady(f"Othor AI follows up to {service.max_metrics()} metrics.")
    if open_conflicts(s):
        raise NotReady("Settle each conflict first.")
    if any(r.changed for r in drafts.stored(s)):
        raise NotReady("Accept or keep each redrafted definition first.")
    from othor.jobs.draft_job import records_from_setup

    changes: list[service.Change] = []
    rows = [r for r in drafts.stored(s) if r.state != "skipped"]
    _attach_breakdowns(s, company, rows)
    broken = _not_compiling(s, company, rows)
    for r in rows:
        body = dict(r.body)
        if r.key in broken:  # kept as a question for Home, never a broken spec
            body.update(status="needs_source", needs=broken[r.key][:200])
        if body.get("status") == "active":
            body.update(confidence="high", confirmed_by=actor, confirmed_at=today.isoformat())
        ref = {
            "setup_draft": str(r.id),
            "origin": r.origin.get("by"),
            "refs": [f"{x.get('upload')} · {x.get('locator')}" for x in r.refs or []],
        }
        changes.append(service.put("metric", r.key, validate_body("metric", body), ref))
    for (type_, key), body in sorted(records_from_setup(s).items()):
        changes.append(service.put(type_, key, validate_body(type_, body), {"setup": True}))
    msg = s.scalars(sa.select(BusinessMessage).order_by(BusinessMessage.version.desc())).first()
    if msg is not None and msg.text.strip():
        changes.append(
            service.put(
                "business_fact",
                "business_message",
                {"text": msg.text.strip()[:4000], "kind": "description"},
                {"business_message": msg.version},
            )
        )
    if changes:
        service.apply(s, changes, actor=actor, reason="Setup", allow_empty=True)
    v = service.confirm(s, actor=actor)
    compile_version(s, company, 1, actor=actor)
    enqueue(
        s,
        "run",
        {"kind": "dry", "run_date": (run_date or today).isoformat()},
        dedupe_key=FIRST_RUN_KEY,
    )
    for r in rows:
        r.state, r.frozen = "confirmed", True
    st = s.scalars(sa.select(SetupState)).first()
    if st is not None and st.current_step in ("metrics", "sources", "business", "upload"):
        before = st.current_step
        st.current_step = "first-run"
        record_event(
            s,
            actor=actor,
            action="onboarding.step_saved",
            object_type="setup_state",
            object_id=company.id,
            before={"step": before},
            after={"step": "first-run"},
        )
    s.flush()
    return v


def _attach_breakdowns(s: Session, company: Company, rows: list[SetupDraft]) -> None:
    """Metrics drafted before the breakdowns existed get the ones they can reach (12 Nov 2026)."""
    from othor.jobs.draft_job import records_from_setup
    from othor.ontology.compiler.api import company_settings
    from othor.setup.compute import attach_breakdowns
    from othor.setup.inputs import gather

    inp = gather(s)
    recs = {**records_from_setup(s), **{("metric", r.key): r.body for r in rows}}
    got = attach_breakdowns(inp.base, recs, inp.cat, company_settings(company))
    for r in rows:
        if r.key in got:
            r.body = {**r.body, "breakdowns": got[r.key]}


def _not_compiling(s: Session, company: Company, rows: list[SetupDraft]) -> dict[str, str]:
    from othor.jobs.draft_job import records_from_setup
    from othor.ontology.compiler.api import company_settings
    from othor.setup.compute import compile_drafts
    from othor.setup.inputs import gather

    inp = gather(s)
    metric = {("metric", r.key): r.body for r in rows if r.body.get("status") == "active"}
    _, errors = compile_drafts(
        inp.base, {**records_from_setup(s), **metric}, inp.cat, company_settings(company)
    )
    return errors


def counts(rows: list[SetupDraft]) -> dict[str, int]:
    out = {"confident": 0, "check": 0, "conflict": 0}
    for r in rows:
        if r.state == "skipped":
            continue
        if r.confidence == "conflict":
            out["conflict"] += 1
        elif r.confidence == "high" or r.state == "confirmed":
            out["confident"] += 1
        else:
            out["check"] += 1
    return out


def summary(rows: list[SetupDraft]) -> dict[str, Any]:
    c = counts(rows)
    return {**c, "ready": c["confident"], "total": sum(c.values())}


def confirm_more(
    s: Session,
    company: Company,
    keys: list[str],
    *,
    actor: str,
    today: dt.date,
    reason: str,
) -> OntologyVersion | None:
    """After setup (6h, a chat's Apply): the chosen drafts and the records setup holds become one
    new Ontology version, compiled. A draft that doesn't compile is refused, never stored broken.
    Confirmed drafts stay as they are."""
    from othor.jobs.draft_job import records_from_setup
    from othor.ontology.compiler.api import compile_version

    if not service.confirmed(s):
        raise NotReady("Confirm setup first.")
    if drafting(s):
        raise NotReady("Drafting is still running.")
    rows = [
        r
        for r in drafts.stored(s)
        if r.key in set(keys) and r.state not in ("skipped", "confirmed") and not r.changed
    ]
    if not rows:
        return None
    broken = _not_compiling(s, company, rows)
    rows = [r for r in rows if r.key not in broken and r.body.get("status") == "active"]
    if not rows:
        raise NotReady("These metrics can't be computed yet.")
    changes: list[service.Change] = []
    for r in rows:
        body = {
            **r.body,
            "confidence": "high",
            "confirmed_by": actor,
            "confirmed_at": today.isoformat(),
        }
        ref = {"setup_draft": str(r.id), "origin": (r.origin or {}).get("by")}
        changes.append(service.put("metric", r.key, validate_body("metric", body), ref))
    for (type_, key), body in sorted(records_from_setup(s).items()):
        changes.append(service.put(type_, key, validate_body(type_, body), {"setup": True}))
    v = service.apply(s, changes, actor=actor, reason=reason)
    if v is not None:
        compile_version(s, company, v.number, actor=actor)
    for r in rows:
        r.state, r.frozen = "confirmed", True
    record_event(
        s,
        actor=actor,
        action="setup.metrics_confirmed",
        object_type="ontology_version",
        object_id=v.id if v is not None else "none",
        after={"keys": [r.key for r in rows], "version": v.number if v else None},
    )
    s.flush()
    return v


# --- 6h after setup (M11c G): stage rows, then add them as one version --------------------------


def is_staged(row: SetupDraft) -> bool:
    return bool((row.origin or {}).get("staged"))


def can_stage(row: SetupDraft) -> bool:
    """A later source's draft the owner can confirm now: active, no open conflict, not redrafted."""
    return (
        row.state not in ("skipped", "confirmed")
        and row.confidence != "conflict"
        and not row.changed
        and row.body.get("status") == "active"
    )


def stage(s: Session, row: SetupDraft, *, actor: str, on: bool = True) -> bool:
    """Confirm (or Undo) one row in the app's Metrics to confirm panel. Nothing goes into the
    Ontology until the owner adds the staged rows; each stage and unstage is an event."""
    if (on and not can_stage(row)) or is_staged(row) == on:
        return False
    origin = {k: v for k, v in (row.origin or {}).items() if k != "staged"}
    if on:
        origin["staged"] = True
    row.origin = origin
    record_event(
        s,
        actor=actor,
        action="source.metric_staged" if on else "source.metric_unstaged",
        object_type="setup_draft",
        object_id=row.id,
        after={"key": row.key},
    )
    return True


def add_staged(
    s: Session,
    company: Company,
    rows: list[SetupDraft],
    *,
    actor: str,
    today: dt.date,
    reason: str,
) -> OntologyVersion | None:
    """ "Add N to the Ontology": the staged rows become one new version (confirm_more), with one
    event per metric added."""
    keys = [r.key for r in rows if is_staged(r)]
    v = confirm_more(s, company, keys, actor=actor, today=today, reason=reason)
    if v is None:
        return None
    for r in rows:
        if r.key in keys and r.state == "confirmed":
            r.origin = {k: x for k, x in (r.origin or {}).items() if k != "staged"}
            record_event(
                s,
                actor=actor,
                action="source.metric_added",
                object_type="setup_draft",
                object_id=r.id,
                after={"key": r.key, "version": v.number},
            )
    s.flush()
    return v


def undo_added(
    s: Session, company: Company, number: int, rows: list[SetupDraft], *, actor: str
) -> OntologyVersion | None:
    """The toast's Undo: a new version putting back what `number` added, and its rows back in
    the panel to confirm again."""
    v = s.scalars(sa.select(OntologyVersion).where(OntologyVersion.number == number)).one()
    keys = {d["key"] for d in v.diff.get("changes", []) if d.get("type") == "metric"}
    nv = service.undo(s, number, actor=actor)
    if nv is not None:
        compile_version(s, company, nv.number, actor=actor)
    for r in rows:
        if r.key in keys and r.state == "confirmed":
            r.state, r.frozen = "needs_confirmation", False
            record_event(
                s,
                actor=actor,
                action="setup.draft_unconfirmed",
                object_type="setup_draft",
                object_id=r.id,
                before={"state": "confirmed"},
                after={"state": r.state, "undo": number},
            )
    s.flush()
    return nv
