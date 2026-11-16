# SPDX-License-Identifier: Apache-2.0
"""Source actions shared by the onboarding and app pages (docs/05). Every action writes an event
row; every use change writes a `table_use` Ontology record. Callers hold the tenant scope.

Use menu rules (board 2ke):
- Candidate (metric source or dimension): switch type, or ignore with a reason.
- Ignored by the scan: use instead as a metric source or dimension; the scan's reason stays.
- Ignored by the owner: change the reason or bring it back.
- Excluded: only "Use without personal columns" or "Keep excluded". Only the scan can exclude.
- Not read yet (queued or reading): no menu.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.connectors import credentials, kinds
from othor.connectors.classify import SCAN_REASONS, USER_REASONS
from othor.connectors.models import Scan, SheetTab, Source, SourceSchema, SourceTable
from othor.core.crypto import InstanceKey
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.core.localtime import local_date
from othor.core.models import Company, utcnow
from othor.jobs.queue import enqueue
from othor.ontology import table_use

USES = ("metric_source", "dimension")
USE_LABEL = {"metric_source": "Metric source", "dimension": "Dimension"}
SHEET_REASONS = {"copy": "Copy of another tab", "no_table": "No table", "old": "Old"}


class ActionRefused(ValueError):
    """The Use menu doesn't offer this for the table's current state."""


def reason_label(code: str | None) -> str:
    if code is None:
        return ""
    return USER_REASONS.get(code) or SCAN_REASONS.get(code) or SHEET_REASONS.get(code) or code


# --- creating and connecting ---------------------------------------------------------------


def unique_name(s: Session, name: str, *, exclude: uuid.UUID | None = None) -> str:
    """A name no other source of the company has: the Ontology names sources, so two with the
    same name would collide ("Google Sheets", "Google Sheets 2"; owner, 11 Nov 2026). Inside
    the caller's tenant scope; draft sources count, deleted ones don't; `exclude` is the source
    being renamed."""
    q = sa.select(Source.name).where(Source.deleted_at.is_(None))
    if exclude is not None:
        q = q.where(Source.id != exclude)
    taken = set(s.scalars(q))
    if name not in taken:
        return name
    n = 2
    while f"{name} {n}" in taken:
        n += 1
    return f"{name} {n}"


def create_sql_source(
    s: Session,
    key: InstanceKey,
    company_id: uuid.UUID,
    *,
    kind: str,
    config: dict[str, Any],
    password: str,
    actor: str,
    in_setup: bool,
) -> Source:
    """A draft source with its encrypted password; Test connection runs on it."""
    src = Source(
        kind=kind,
        name=unique_name(s, config["database"]),
        status="draft",
        config=config,
        in_setup=in_setup,
        last_test={"pending": True},
    )
    s.add(src)
    s.flush()
    credentials.store(s, key, company_id, src.id, {"password": password})
    record_event(
        s,
        actor=actor,
        action="source.created",
        object_type="source",
        object_id=src.id,
        after={"kind": kind, **{k: v for k, v in config.items() if k != "password"}},
    )
    return src


def update_sql_source(
    s: Session,
    key: InstanceKey,
    src: Source,
    *,
    config: dict[str, Any],
    password: str | None,
    actor: str,
) -> None:
    before = dict(src.config)
    src.config = config
    # the name follows the database only when the database changed, and never onto a name in
    # use (indigo2, 12 Nov 2026: saving the details again turned "sales_dw 2" into "sales_dw")
    if config.get("database") != before.get("database"):
        src.name = unique_name(s, config["database"], exclude=src.id)
    src.last_test = {"pending": True}
    if password:
        credentials.store(s, key, src.company_id, src.id, {"password": password})
    record_event(
        s,
        actor=actor,
        action="source.credentials_changed",
        object_type="source",
        object_id=src.id,
        before=before,
        after={**config, "password": "changed" if password else "unchanged"},
    )


def request_test(s: Session, src: Source) -> None:
    src.last_test = {"pending": True}
    enqueue(s, "scan", {"source_id": str(src.id), "mode": "test"})


def start_scan(s: Session, src: Source, *, actor: str, tabs: dict[str, Any] | None = None) -> Scan:
    prev = s.scalars(
        sa.select(Scan).where(Scan.source_id == src.id).order_by(Scan.created_at.desc()).limit(1)
    ).first()
    sc = Scan(source_id=src.id, previous_scan_id=prev.id if prev else None, progress={})
    s.add(sc)
    s.flush()
    payload: dict[str, Any] = {"source_id": str(src.id), "scan_id": str(sc.id)}
    if tabs:
        payload["tabs"] = tabs
    enqueue(s, "scan", payload)
    record_event(
        s,
        actor=actor,
        action="scan.requested",
        object_type="source",
        object_id=src.id,
        after={"scan_id": str(sc.id)},
    )
    return sc


def connect(s: Session, src: Source, *, actor: str, now: datetime) -> Scan:
    """Connect after a passing test: the scan starts; the source is connected when it ends."""
    if not (src.last_test or {}).get("ok"):
        raise ActionRefused("Test the connection first.")
    before = src.status
    src.status = "scanning"
    src.connected_by = actor
    record_event(
        s,
        actor=actor,
        action="source.connected",
        object_type="source",
        object_id=src.id,
        before={"status": before},
        after={"status": "scanning", "at": now.isoformat()},
    )
    return start_scan(s, src, actor=actor)


def latest_scan(s: Session, source_id: uuid.UUID) -> Scan | None:
    return s.scalars(
        sa.select(Scan).where(Scan.source_id == source_id).order_by(Scan.created_at.desc()).limit(1)
    ).first()


def skip_schema(s: Session, src: Source, schema: str, *, actor: str, skip: bool = True) -> bool:
    """Stop (or, with skip=False, start again) reading one schema. True when it changed. A
    skipped schema's tables leave the catalog at the next scan; un-skipped, they come back."""
    row = s.scalars(
        sa.select(SourceSchema).where(SourceSchema.source_id == src.id, SourceSchema.name == schema)
    ).one()
    if row.skipped == skip:
        return False
    row.skipped = skip
    record_event(
        s,
        actor=actor,
        action="scan.schema_skipped" if skip else "scan.schema_unskipped",
        object_type="source",
        object_id=src.id,
        before={"schema": schema, "skipped": not skip},
        after={"schema": schema, "skipped": skip},
    )
    return True


def set_schemas(s: Session, src: Source, read: set[str], *, actor: str) -> bool:
    """The Schemas panel's Save (M11c): read the ticked schemas, skip the others; a rescan
    starts when anything changed. One event per schema changed. True when a rescan started."""
    rows = s.scalars(sa.select(SourceSchema).where(SourceSchema.source_id == src.id)).all()
    changed = [
        x.name for x in rows if skip_schema(s, src, x.name, actor=actor, skip=x.name not in read)
    ]
    if changed:
        start_scan(s, src, actor=actor)
    return bool(changed)


# --- the Use menu ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MenuState:
    kind: str  # candidate · scan_ignored · user_ignored · excluded · not_ready
    current: str | None


def menu_state(t: SourceTable) -> MenuState:
    if t.state != "done":
        return MenuState("not_ready", None)
    if t.use == "excluded":
        return MenuState("excluded", None)
    if t.use == "ignored":
        return MenuState("user_ignored" if t.use_set_by == "user" else "scan_ignored", t.use_reason)
    return MenuState("candidate", t.use)


def _write_use(
    s: Session, t: SourceTable, *, actor: str, now: datetime, action: str, before: dict[str, Any]
) -> uuid.UUID:
    t.use_set_at = now
    rec = table_use.write(
        s,
        source_id=t.source_id,
        stable_key=t.stable_key,
        body={
            "source_id": str(t.source_id),
            "schema": t.schema,
            "table": t.name,
            "use": t.use,
            "reason": t.use_reason,
            "without_personal": t.without_personal,
        },
        actor=actor,
        source_ref={"owner": actor, "at": now.isoformat()},
    )
    ev = record_event(
        s,
        actor=actor,
        action=action,
        object_type="source_table",
        object_id=t.id,
        before=before,
        after={
            "use": t.use,
            "reason": t.use_reason,
            "without_personal": t.without_personal,
            "record": str(rec.id),
        },
    )
    return ev.id


def set_use(
    s: Session, t: SourceTable, use: str, reason: str | None, *, actor: str, now: datetime
) -> uuid.UUID:
    """Owner changes a table's use from its menu. Returns the event id (for Undo)."""
    st = menu_state(t)
    if st.kind in ("not_ready", "excluded"):
        raise ActionRefused("This table's use can't be changed from this menu.")
    if use == "ignored":
        if reason not in USER_REASONS:
            raise ActionRefused("Pick a reason.")
    elif use in USES:
        reason = None
    else:
        raise ActionRefused("Unknown use.")
    before = {"use": t.use, "reason": t.use_reason, "set_by": t.use_set_by}
    if (t.use, t.use_reason) == (use, reason):
        return _write_use(s, t, actor=actor, now=now, action="table_use.unchanged", before=before)
    t.use, t.use_reason = use, reason
    t.use_set_by, t.use_set_by_user = "user", actor
    return _write_use(s, t, actor=actor, now=now, action="table_use.changed", before=before)


def bring_back(s: Session, t: SourceTable, use: str, *, actor: str, now: datetime) -> uuid.UUID:
    """2kb: bring an ignored table back as a metric source or dimension."""
    if t.use != "ignored":
        raise ActionRefused("Only ignored tables can be brought back.")
    return set_use(s, t, use, None, actor=actor, now=now)


def use_without_personal(
    s: Session, t: SourceTable, use: str, *, actor: str, now: datetime
) -> uuid.UUID:
    """2kc: an Excluded table used without its personal columns (they stay never read)."""
    if t.use != "excluded":
        raise ActionRefused("Only excluded tables offer this.")
    if use not in USES:
        raise ActionRefused("Unknown use.")
    before = {"use": t.use, "reason": t.use_reason, "without_personal": t.without_personal}
    t.use, t.use_reason, t.without_personal = use, None, True
    t.use_set_by, t.use_set_by_user = "user", actor
    return _write_use(
        s, t, actor=actor, now=now, action="table_use.without_personal", before=before
    )


def keep_excluded(s: Session, t: SourceTable, *, actor: str, now: datetime) -> uuid.UUID | None:
    if t.scan_use != "excluded":
        raise ActionRefused("Only the scan can exclude a table.")
    if t.use == "excluded":
        return None
    before = {"use": t.use, "reason": t.use_reason, "without_personal": t.without_personal}
    t.use, t.use_reason, t.without_personal, t.use_set_by = "excluded", None, False, "scan"
    return _write_use(s, t, actor=actor, now=now, action="table_use.kept_excluded", before=before)


def undo_use(
    s: Session, t: SourceTable, before: dict[str, Any], *, actor: str, now: datetime
) -> uuid.UUID:
    """Undo of a use change is a new change back to the previous state (docs/03)."""
    prev = {"use": t.use, "reason": t.use_reason, "without_personal": t.without_personal}
    t.use = before.get("use")
    t.use_reason = before.get("reason")
    t.without_personal = bool(before.get("without_personal", False))
    t.use_set_by = before.get("set_by") or ("scan" if t.use == t.scan_use else "user")
    return _write_use(s, t, actor=actor, now=now, action="table_use.undone", before=prev)


# --- Sheets ----------------------------------------------------------------------------------


def create_sheets_source(
    s: Session,
    key: InstanceKey,
    company_id: uuid.UUID,
    *,
    files: list[dict[str, str]],
    secret: dict[str, Any],
    account: str,
    actor: str,
    in_setup: bool,
) -> Source:
    name = files[0].get("title") or "Google Sheets" if files else "Google Sheets"
    name = unique_name(s, name)
    src = Source(
        kind="gsheets",
        name=name,
        status="scanning",
        in_setup=in_setup,
        config={"files": files, "account": account},
        connected_by=actor,
    )
    s.add(src)
    s.flush()
    credentials.store(s, key, company_id, src.id, secret)
    record_event(
        s,
        actor=actor,
        action="source.created",
        object_type="source",
        object_id=src.id,
        after={"kind": "gsheets", "files": [f["id"] for f in files], "account": account},
    )
    return src


def confirm_tab(s: Session, tab: SheetTab, *, actor: str, now: datetime, fingerprint: str) -> None:
    before = {"status": tab.status, "header_row": tab.header_row, "last_row": tab.last_row}
    tab.status, tab.confirmed_by, tab.confirmed_at = "confirmed", actor, now
    tab.header_fingerprint = fingerprint
    if tab.shape and "preview" in tab.shape:
        tab.shape = {k: v for k, v in tab.shape.items() if k != "preview"}  # rows not kept
    record_event(
        s,
        actor=actor,
        action="sheet_tab.confirmed",
        object_type="sheet_tab",
        object_id=tab.id,
        before=before,
        after={
            "status": "confirmed",
            "header_row": tab.header_row,
            "last_row": tab.last_row,
            "range": tab.range,
            "fingerprint": fingerprint,
        },
    )


def finish_sheets(s: Session, src: Source, *, actor: str, now: datetime) -> None:
    """Every selected tab confirmed → the source is connected; first read tonight."""
    tabs = s.scalars(
        sa.select(SheetTab).where(SheetTab.source_id == src.id, SheetTab.selected.is_(True))
    ).all()
    if not tabs or any(t.status != "confirmed" for t in tabs):
        raise ActionRefused("Check every selected tab first.")
    before = src.status
    src.status, src.connected_at = "connected", now
    record_event(
        s,
        actor=actor,
        action="source.connected",
        object_type="source",
        object_id=src.id,
        before={"status": before},
        after={"status": "connected", "tabs": len(tabs)},
    )


# --- manage ------------------------------------------------------------------------------------


def pause(s: Session, src: Source, paused: bool, *, actor: str) -> None:
    before = src.status
    if paused and src.status == "connected":
        src.status = "paused"
    elif not paused and src.status == "paused":
        src.status = "connected"
    else:
        return
    record_event(
        s,
        actor=actor,
        action="source.paused" if paused else "source.resumed",
        object_type="source",
        object_id=src.id,
        before={"status": before},
        after={"status": src.status},
    )


def read_now(s: Session, src: Source, *, actor: str, now: datetime | None = None) -> None:
    """Read now (10r). Once the source feeds a confirmed metric, a source-scoped manual run: it
    reads this source and refreshes the series that depend on it, in the ledger like any run,
    with no detectors, forecasts, briefs or emails (5 Nov 2026). Before that (setup), Sheets tabs
    are read into the store and a database is tested and re-scanned."""
    if _in_spec(s, src):
        company = s.scalars(sa.select(Company).where(Company.id == src.company_id)).one()
        enqueue(
            s,
            "run",
            {
                "kind": "manual",
                "run_date": local_date(company, now or utcnow()).isoformat(),
                "scope": {"source_id": str(src.id)},
            },
        )
    elif kinds.is_local(src.kind):  # fetched, not read in place (Sheets, a plug-in kind)
        enqueue(s, "read", {"source_id": str(src.id)})
    else:
        request_test(s, src)
        start_scan(s, src, actor=actor)
    record_event(s, actor=actor, action="source.read_now", object_type="source", object_id=src.id)


def _in_spec(s: Session, src: Source) -> bool:
    from othor.ontology import service as ontology
    from othor.ontology.models import Spec

    v = ontology.current_number(s)
    if not v:
        return False
    spec = s.scalars(
        sa.select(Spec).where(Spec.ontology_version == v).order_by(Spec.created_at.desc())
    ).first()
    if spec is None:
        return False
    return any(m.get("id") == str(src.id) for m in spec.compiled.get("sources", {}).values())


def mark_disconnected(s: Session, source_id: uuid.UUID, reason: str, *, now: datetime) -> None:
    """The system found the credentials no longer work (token revoked, access removed)."""
    row = s.scalars(sa.select(Source).where(Source.id == source_id)).one()
    before = row.status
    row.status = "disconnected"
    row.disconnected_at = now
    row.disconnect_reason = reason[:300]
    record_event(
        s,
        actor=SYSTEM_ACTOR,
        action="source.disconnected",
        object_type="source",
        object_id=source_id,
        before={"status": before},
        after={"status": "disconnected", "reason": reason[:300]},
    )


def disconnect(s: Session, src: Source, *, actor: str, now: datetime) -> None:
    """Credentials deleted; history (scans, tables, events, records) kept."""
    before = src.status
    credentials.delete(s, src.id)
    src.status, src.disconnected_at = "disconnected", now
    src.disconnect_reason = "Disconnected by the owner"
    src.deleted_at = now
    record_event(
        s,
        actor=actor,
        action="source.disconnected",
        object_type="source",
        object_id=src.id,
        before={"status": before},
        after={"status": "disconnected", "credentials": "deleted"},
    )


def acknowledge_schema_change(s: Session, src: Source, *, actor: str) -> None:
    if not src.schema_change or src.schema_change.get("acknowledged"):
        return
    before = dict(src.schema_change)
    src.schema_change = {**src.schema_change, "acknowledged": True}
    record_event(
        s,
        actor=actor,
        action="schema_change.acknowledged",
        object_type="source",
        object_id=src.id,
        before=before,
        after=src.schema_change,
    )
