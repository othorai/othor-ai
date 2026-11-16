# SPDX-License-Identifier: Apache-2.0
"""The one write path into the Ontology: every change is a new version (docs/03).

`apply` takes a list of changes (put a record body under a (type, key), or retire it), closes the
records it replaces (`version_to`), adds the new ones (`version_from`), stores a readable diff
and writes an event. Undo of a version and Restore of an old version are new versions too; no
record is ever deleted. Callers hold the tenant scope and commit.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.config import get_settings
from othor.core.events import record_event
from othor.core.models import Event
from othor.ontology import content
from othor.ontology.models import OntologyRecord, OntologyVersion
from othor.ontology.types import validate_body

Key = tuple[str, str]  # (type, key)


@dataclass(frozen=True)
class Change:
    op: Literal["put", "retire"]
    type: str
    key: str
    body: dict[str, Any] = field(default_factory=dict)
    source_ref: dict[str, Any] | None = None


def put(
    type_: str, key: str, body: dict[str, Any], source_ref: dict[str, Any] | None = None
) -> Change:
    return Change("put", type_, key, body, source_ref)


def retire(type_: str, key: str) -> Change:
    return Change("retire", type_, key)


class NothingToApply(ValueError):
    pass


class MetricCeiling(ValueError):
    """A change that would put more metrics in use than the company's ceiling (M15.1)."""


def max_metrics() -> int:
    return get_settings().max_metrics


def ceiling_text(verb: str = "Retire") -> str:
    return f"Othor AI follows up to {max_metrics()} metrics. {verb} one before adding another."


def _in_use(body: dict[str, Any] | None) -> bool:
    return body is not None and body.get("status", "active") == "active"


def _check_ceiling(
    present: list[OntologyRecord],
    planned: list[tuple[Change, OntologyRecord | None, dict[str, Any] | None]],
) -> None:
    """Refuse a change that raises the metrics in use past the ceiling. A company already over it
    (a lowered setting) can still edit and retire; it just can't add."""
    if not any(c.type == "metric" for c, _p, _b in planned):
        return
    now = {r.key for r in present if r.type == "metric" and _in_use(r.body)}
    after = set(now)
    for c, _prev, body in planned:
        if c.type == "metric":
            (after.add if _in_use(body) else after.discard)(c.key)
    limit = max_metrics()
    if len(after) > limit and len(after) > len(now):
        raise MetricCeiling(ceiling_text())


def current_number(s: Session) -> int:
    n = s.scalar(sa.select(sa.func.max(OntologyVersion.number)))
    return int(n or 0)


def confirmed(s: Session) -> bool:
    """True once setup is confirmed (version 1 exists). Before that, changes are drafts."""
    return current_number(s) > 0


def active(s: Session, types: Iterable[str] | None = None) -> list[OntologyRecord]:
    """Live records (after Confirm)."""
    q = sa.select(OntologyRecord).where(OntologyRecord.status == "active")
    if types is not None:
        q = q.where(OntologyRecord.type.in_(list(types)))
    return list(s.scalars(q.order_by(OntologyRecord.type, OntologyRecord.key)))


def current(s: Session, types: Iterable[str] | None = None) -> list[OntologyRecord]:
    """What is in effect now: live records after Confirm, the draft records before it."""
    q = sa.select(OntologyRecord).where(OntologyRecord.status.in_(("active", "draft")))
    if types is not None:
        q = q.where(OntologyRecord.type.in_(list(types)))
    return list(s.scalars(q.order_by(OntologyRecord.type, OntologyRecord.key)))


def active_at(s: Session, version: int, types: Iterable[str] | None = None) -> list[OntologyRecord]:
    """The records that were live at `version` (version_from ≤ v < version_to)."""
    q = sa.select(OntologyRecord).where(
        OntologyRecord.version_from.is_not(None),
        OntologyRecord.version_from <= version,
        sa.or_(OntologyRecord.version_to.is_(None), OntologyRecord.version_to > version),
    )
    if types is not None:
        q = q.where(OntologyRecord.type.in_(list(types)))
    return list(s.scalars(q.order_by(OntologyRecord.type, OntologyRecord.key)))


def history(s: Session) -> list[OntologyVersion]:
    return list(s.scalars(sa.select(OntologyVersion).order_by(OntologyVersion.number.desc())))


def apply(
    s: Session,
    changes: Sequence[Change],
    *,
    actor: str,
    reason: str,
    source_ref: dict[str, Any] | None = None,
    allow_empty: bool = False,
) -> OntologyVersion | None:
    """Validate, then write one new version. Changes that leave a record as it is are skipped;
    if nothing changes, no version is written (returns None) unless `allow_empty`.

    Before Confirm (no version yet) nothing is versioned: the changes are written as draft
    records (the replaced draft is retired, never deleted), an event is logged, and None is
    returned. Confirm turns the drafts into version 1."""
    draft_mode = not confirmed(s)
    present = current(s, {c.type for c in changes})
    current_ = {(r.type, r.key): r for r in present}
    planned: list[tuple[Change, OntologyRecord | None, dict[str, Any] | None]] = []
    cites: list[tuple[Change, OntologyRecord]] = []
    body: dict[str, Any] | None
    seen: set[Key] = set()
    for c in changes:
        k = (c.type, c.key)
        if k in seen:
            raise ValueError(f"{c.type} {c.key!r} is changed twice in one version")
        seen.add(k)
        prev = current_.get(k)
        if c.op == "put":
            body = validate_body(c.type, c.body)
            if prev is not None and prev.body == body:
                # the same meaning: kept only as a better citation (M10b, "Source updated")
                if draft_mode or not c.source_ref or c.source_ref == prev.source_ref:
                    continue
                cites.append((c, prev))
                continue
            planned.append((c, prev, body))
        else:
            if prev is None:
                continue
            planned.append((c, prev, None))
    _check_ceiling(present, planned)
    if draft_mode:
        _apply_drafts(s, planned, actor=actor, reason=reason, source_ref=source_ref)
        return None
    if not planned and not cites and not allow_empty:
        return None
    if planned and not cites and not allow_empty and _same_content(s, planned):
        return None  # the changes cancel out: "No change" (M10b)
    n = current_number(s) + 1
    diff = []
    for c, prev in cites:
        prev.status, prev.version_to = "retired", n
        s.add(
            OntologyRecord(
                type=c.type,
                key=c.key,
                body=prev.body,
                status="active",
                source_ref=c.source_ref,
                created_by=actor,
                supersedes_id=prev.id,
                version_from=n,
            )
        )
        diff.append(
            {
                "op": "cite",
                "type": c.type,
                "key": c.key,
                "before": prev.source_ref,
                "after": c.source_ref,
            }
        )
    if cites and not planned:
        reason = "Source updated"
    for c, prev, body in planned:
        if prev is not None:
            prev.status, prev.version_to = "retired", n
        if body is not None:
            s.add(
                OntologyRecord(
                    type=c.type,
                    key=c.key,
                    body=body,
                    status="active",
                    source_ref=c.source_ref or source_ref,
                    created_by=actor,
                    supersedes_id=prev.id if prev else None,
                    version_from=n,
                )
            )
        diff.append(
            {
                "op": "add" if prev is None else ("retire" if body is None else "change"),
                "type": c.type,
                "key": c.key,
                "before": prev.body if prev is not None else None,
                "after": body,
            }
        )
    diff.sort(key=lambda d: (d["type"], d["key"]))
    v = OntologyVersion(
        number=n,
        created_by=actor,
        reason=reason[:300],
        source_ref=source_ref,
        diff={"changes": diff},
    )
    s.add(v)
    s.flush()
    v.content_hash = content.version_hash(s, n)
    record_event(
        s,
        actor=actor,
        action="ontology.version_created",
        object_type="ontology_version",
        object_id=v.id,
        after={"number": n, "reason": reason[:300], "changes": len(diff)},
    )
    return v


def _apply_drafts(
    s: Session,
    planned: list[tuple[Change, OntologyRecord | None, dict[str, Any] | None]],
    *,
    actor: str,
    reason: str,
    source_ref: dict[str, Any] | None,
) -> None:
    if not planned:
        return
    diff = []
    for c, prev, body in planned:
        if prev is not None:
            prev.status = "retired"  # a replaced draft: never live, kept
        if body is not None:
            s.add(
                OntologyRecord(
                    type=c.type,
                    key=c.key,
                    body=body,
                    status="draft",
                    source_ref=c.source_ref or source_ref,
                    created_by=actor,
                    supersedes_id=prev.id if prev else None,
                )
            )
        diff.append(
            {
                "op": "add" if prev is None else ("retire" if body is None else "change"),
                "type": c.type,
                "key": c.key,
            }
        )
    s.flush()
    record_event(
        s,
        actor=actor,
        action="ontology.draft_changed",
        object_type="ontology",
        object_id="setup",
        after={
            "reason": reason[:300],
            "changes": sorted(diff, key=lambda d: (d["type"], d["key"])),
        },
    )


class AlreadyConfirmed(ValueError):
    pass


def confirm(s: Session, *, actor: str, reason: str = "Setup confirmed") -> OntologyVersion:
    """Setup's Confirm: every draft record becomes live as version 1."""
    if confirmed(s):
        raise AlreadyConfirmed("the Ontology is already confirmed")
    drafts = list(
        s.scalars(
            sa.select(OntologyRecord)
            .where(OntologyRecord.status == "draft")
            .order_by(OntologyRecord.type, OntologyRecord.key)
        )
    )
    for r in drafts:
        r.status, r.version_from = "active", 1
    trail = len(setup_trail(s))
    v = OntologyVersion(
        number=1,
        created_by=actor,
        reason=reason,
        source_ref={"setup_changes": trail},
        diff={
            "changes": [
                {"op": "add", "type": r.type, "key": r.key, "before": None, "after": r.body}
                for r in drafts
            ]
        },
    )
    s.add(v)
    s.flush()
    v.content_hash = content.version_hash(s, 1)
    record_event(
        s,
        actor=actor,
        action="ontology.confirmed",
        object_type="ontology_version",
        object_id=v.id,
        after={"number": 1, "records": len(drafts), "setup_changes": trail},
    )
    return v


def setup_trail(s: Session) -> list[Event]:
    """The changes made during setup, before Confirm (History: "Setup · n changes")."""
    return list(
        s.scalars(
            sa.select(Event).where(Event.action == "ontology.draft_changed").order_by(Event.at)
        )
    )


def _state_at(s: Session, version: int, keys: Iterable[Key]) -> dict[Key, dict[str, Any] | None]:
    wanted = set(keys)
    live = {(r.type, r.key): r.body for r in active_at(s, version, {t for t, _ in wanted})}
    return {k: live.get(k) for k in wanted}


def undo(s: Session, number: int, *, actor: str) -> OntologyVersion | None:
    """A new version putting every record touched by `number` back to its state before it."""
    v = s.scalars(sa.select(OntologyVersion).where(OntologyVersion.number == number)).one()
    keys = [(d["type"], d["key"]) for d in v.diff.get("changes", [])]
    before = _state_at(s, number - 1, keys)
    refs = {
        (r.type, r.key): r.source_ref
        for r in active_at(s, number - 1)
        if (r.type, r.key) in set(keys)
    }
    changes = [
        put(t, k, b, refs.get((t, k))) if b is not None else retire(t, k)
        for (t, k), b in sorted(before.items())
    ]
    nv = apply(
        s,
        changes,
        actor=actor,
        reason=f"Undo v{number}: {v.reason}"[:300],
        source_ref={"undo": number},
    )
    if nv is not None:
        record_event(
            s,
            actor=actor,
            action="ontology.undone",
            object_type="ontology_version",
            object_id=nv.id,
            before={"number": number},
            after={"number": nv.number},
        )
    return nv


def restore(s: Session, number: int, *, actor: str) -> OntologyVersion | None:
    """A new version whose live records equal those of version `number`."""
    then = {(r.type, r.key): r.body for r in active_at(s, number)}
    now = {(r.type, r.key): r.body for r in active(s)}
    changes = [put(t, k, b) for (t, k), b in sorted(then.items()) if now.get((t, k)) != b]
    changes += [retire(t, k) for (t, k) in sorted(set(now) - set(then))]
    nv = apply(s, changes, actor=actor, reason=f"Restore v{number}", source_ref={"restore": number})
    if nv is not None:
        record_event(
            s,
            actor=actor,
            action="ontology.restored",
            object_type="ontology_version",
            object_id=nv.id,
            before={"number": number},
            after={"number": nv.number},
        )
    return nv


def _same_content(
    s: Session, planned: list[tuple[Change, OntologyRecord | None, dict[str, Any] | None]]
) -> bool:
    """Would these changes leave the live records as they are (they cancel out)?"""
    live = {(r.type, r.key): r.body for r in active(s)}
    before = content.records_hash((t, k, b) for (t, k), b in live.items())
    for c, _prev, body in planned:
        if body is None:
            live.pop((c.type, c.key), None)
        else:
            live[(c.type, c.key)] = body
    return content.records_hash((t, k, b) for (t, k), b in live.items()) == before
