# SPDX-License-Identifier: Apache-2.0
"""Draft the metrics, find what's in the data, compute today's values, check conflicts (2m → 2n).

payload: {"mode": "draft" | "redraft" | "values" | "source" | "document", "actor": user id,
          "source_id", "for" (mode source), "upload_id" (mode document)}

- draft: by Claude when the model is available; otherwise (or if the call fails) by rules
- redraft ("Redraft with Othor AI"): the model only; locked rows untouched, changes kept as diffs
- values: recompute values and conflicts only (after an edit, Choose or the Add form)

Every source statement is read-only, guarded against personal columns, and logged in run_query
with purpose "draft". No number here comes from a model.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.gateway import model_status
from othor.connectors.models import RunQuery, Source
from othor.connectors.sql.engine import make_engine
from othor.connectors.sql.query_log import QueryLog
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext
from othor.ontology.compiler.api import company_settings
from othor.setup import conflicts, discover, drafts, uploads
from othor.setup.compute import NotRead, Runner, compile_drafts
from othor.setup.inputs import SetupInputs, creds_loader, gather
from othor.setup.models import SetupDraft, Upload
from othor.setup.rules_draft import rule_drafts

META = ("meta", "drafting")
RECORD_KINDS = ("rule", "vocabulary", "breakdown", "known_pattern", "business_fact", "goal")


def run(ctx: WorkerContext, job: Job) -> None:
    cid = job.company_id
    mode = job.payload.get("mode", "draft")
    actor = job.payload.get("actor", SYSTEM_ACTOR)
    now = ctx.clock()
    today = now.date()
    with ctx.sf() as s, tenant_scope(cid):
        inp = gather(s)
        available, why = model_status(ctx.settings, s, now)
        _meta(s, state="drafting", mode=mode, started=now.isoformat())  # the progress clock (#31)
        s.commit()

    if mode == "document":
        _document(ctx, cid, job.payload, available, actor)
        with ctx.sf() as s, tenant_scope(cid):
            inp = gather(s)  # the document's rules and vocabulary are records now

    if mode == "source":
        with ctx.sf() as s, tenant_scope(cid):
            _from_source(s, inp, job.payload, actor)
            _meta(s, state="computing", mode=mode)
            s.commit()

    if mode in ("draft", "redraft"):
        got, by, why = _draft(ctx, cid, inp, available, why, mode)
        with ctx.sf() as s, tenant_scope(cid):
            if got is not None:
                drafts.store(s, got, actor=actor, redraft=mode == "redraft")
            _meta(s, state="computing", mode=mode, by=by, why=why)
            s.commit()

    with ctx.sf() as s, tenant_scope(cid):
        company = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        creds = creds_loader(s, ctx.key, cid)
        source_ids = {x.name: x.id for x in s.scalars(sa.select(Source))}
        log = QueryLog(personal=inp.personal)
        if mode == "draft":
            _discover(s, inp, creds, log, source_ids, today)
        _values(ctx, s, cid, inp, creds, log, source_ids, company_settings(company), today)
        _conflicts(ctx, s, cid)
        meta = _meta(s, state="done")
        record_event(
            s,
            actor=actor,
            action="setup.values_computed",
            object_type="setup_draft",
            object_id="metrics",
            after={"mode": mode, "by": meta.body.get("by")},
        )
        s.commit()


def _document(
    ctx: WorkerContext, cid: uuid.UUID, payload: dict[str, Any], available: bool, actor: str
) -> None:
    """2ne: redraft with an uploaded document. Rule patterns always; the model's changes too
    when available, each kept only if its quote is in the document."""
    from othor.jobs import upload_job
    from othor.setup import document
    from othor.setup.models import UploadRef

    uid = uuid.UUID(payload["upload_id"])
    with ctx.sf() as s, tenant_scope(cid):
        up = s.scalars(sa.select(Upload).where(Upload.id == uid)).one()
        unread = up.status in ("added", "reading")
    if unread:
        upload_job.read(ctx, uid)
    with ctx.sf() as s, tenant_scope(cid):
        up = s.scalars(sa.select(Upload).where(Upload.id == uid)).one()
        refs = list(
            s.scalars(
                sa.select(UploadRef)
                .where(UploadRef.upload_id == uid)
                .order_by(UploadRef.created_at)
            )
        )
        pages = document.pages_of(refs)
        inp = gather(s)
        records = {**inp.records, **records_from_setup(s)}
        rows = drafts.stored(s)
        changes = document.rule_changes(pages, rows, inp.cat, records)
        model_in = None
        if available and ctx.gateway is not None and pages:
            model_in = {
                "metrics": [
                    {
                        "key": r.key,
                        "name": r.body.get("name"),
                        "definition": r.body,
                        "question": r.note,
                    }
                    for r in rows
                    if not drafts.locked(r) and r.state != "skipped"
                ],
                "tables": drafts.catalog_input(inp.cat),
                "document": [{"page": loc, "text": text[:4000]} for loc, text in pages[:60]],
            }
    if model_in is not None and ctx.gateway is not None:
        res = ctx.gateway.complete(cid, "document_changes", "v1", model_in)
        if res.ok and res.output:
            with ctx.sf() as s, tenant_scope(cid):
                rows = drafts.stored(s)
                seen = {(c.key, c.result) for c in changes}
                for c in document.model_changes(
                    res.output, pages, rows, inp.cat, records, model_in
                ):
                    if (c.key, c.result) not in seen:
                        changes.append(c)
    with ctx.sf() as s, tenant_scope(cid):
        up = s.scalars(sa.select(Upload).where(Upload.id == uid)).one()
        meta = _meta(s)
        n = int(meta.body.get("round") or 1) + 1
        document.apply(s, changes, upload=up, draft_round=n, actor=actor)
        _meta(s, state="computing", mode="document", round=n, document=str(uid))
        s.commit()


def _from_source(s: Session, inp: SetupInputs, payload: dict[str, Any], actor: str) -> None:
    """A source connected from Metrics (2ncb) or Sources (6h): redraft the metric it was
    connected for, and add the drafts it unlocks. By rules; values are computed after."""
    from othor.setup import connect

    sid = uuid.UUID(payload["source_id"])
    src = s.scalars(sa.select(Source).where(Source.id == sid)).one()
    rows = {r.key: r for r in drafts.stored(s)}
    got = connect.draft_for_source(
        inp.cat,
        src.name,
        sid,
        rows,
        payload.get("for"),
        rules=inp.rules,
        vocabulary=inp.vocabulary,
        names=" ".join(str(f.get("title") or "") for f in (src.config or {}).get("files", [])),
    )
    changed = []
    for key, body in got.redrafted:
        row = rows[key]
        before = {"body": row.body, "state": row.state}
        row.body, row.value, row.note = body, None, None
        row.state, row.confidence, row.frozen = "needs_confirmation", "high", False
        row.origin = {
            **(row.origin or {}),
            "by": "rules",
            "source_id": str(sid),
            "was_waiting": True,
        }
        changed.append(key)
        record_event(
            s,
            actor=actor,
            action="setup.redrafted_for_source",
            object_type="setup_draft",
            object_id=row.id,
            before=before,
            after={"body": body, "source_id": str(sid)},
        )
    pos = max([r.position for r in rows.values()] + [0]) + 1
    for i, d in enumerate(got.added):
        s.add(
            SetupDraft(
                kind="metric",
                key=d.key,
                body=d.body,
                origin=d.origin,
                refs=d.refs,
                confidence=d.confidence,
                state=d.state,
                position=pos + i,
            )
        )
    s.flush()
    record_event(
        s,
        actor=actor,
        action="setup.drafted_from_source",
        object_type="source",
        object_id=sid,
        after={"redrafted": changed, "added": [d.key for d in got.added]},
    )


def _meta(s: Session, **kw: Any) -> SetupDraft:
    row = s.scalars(
        sa.select(SetupDraft).where(SetupDraft.kind == META[0], SetupDraft.key == META[1])
    ).first()
    if row is None:
        row = SetupDraft(kind=META[0], key=META[1], body={}, origin={"by": "system"})
        s.add(row)
    row.body = {**(row.body or {}), **kw}
    s.flush()
    return row


def _draft(
    ctx: WorkerContext, cid: uuid.UUID, inp: SetupInputs, available: bool, why: str, mode: str
) -> tuple[list[drafts.Draft] | None, str, str]:
    if available and ctx.gateway is not None:
        with ctx.sf() as s, tenant_scope(cid):
            rows = drafts.stored(s)
        inputs = drafts.model_inputs(
            inp.cat,
            inp.refs,
            inp.message,
            keys=[r.key for r in rows],
            rules=[str(b.get("text") or "") for _, b in inp.rules if b.get("text")],
            confirmed=[r.key for r in rows if drafts.locked(r)],
            breakdowns=inp.breakdowns,
        )
        # the backstop: names personal wherever they appear (a column "name" that is personal
        # in employee but a product name elsewhere is dropped per table by catalog_input)
        everyday = {c["column"].lower() for t in inputs["tables"] for c in t["columns"]}
        personal = tuple(sorted({c for cols in inp.personal.values() for c in cols} - everyday))
        res = ctx.gateway.complete(cid, *drafts.PROMPT, inputs, personal_names=personal)
        if res.ok and res.output is not None:
            by_model = drafts.from_model(
                res.output,
                inp.cat,
                inputs,
                breakdowns={k: str(b.get("column", "")) for k, b in inp.breakdowns.items()},
                rules=inp.rules,
            )
            return by_model, "model", "ok"
        why = "down" if res.guard in ("error", "unavailable") else res.guard
    if mode == "redraft":
        return None, "rules", why  # Redraft needs the model; nothing changes without it
    got = rule_drafts(inp.cat, inp.refs, rules=inp.rules, vocabulary=inp.vocabulary)
    return [drafts.Draft.from_rule(d) for d in got], "rules", why


def _flush(
    s: Session,
    log: QueryLog,
    ran: list[tuple[str, int]],
    source_ids: dict[str, Any],
    draft_id: uuid.UUID | None,
) -> None:
    for source, i in ran:
        q = log.entries[i]
        s.add(
            RunQuery(
                purpose="draft",
                draft_id=draft_id,
                source_id=source_ids[source],
                sql=q.sql,
                rows=q.rows,
                duration_ms=q.duration_ms,
                hash=q.hash,
                params={
                    k: v.isoformat() if isinstance(v, dt.date) else v for k, v in q.params.items()
                }
                or None,
            )
        )


def _discover(
    s: Session,
    inp: SetupInputs,
    creds: dict[str, Any],
    log: QueryLog,
    source_ids: dict[str, Any],
    today: dt.date,
) -> None:
    engines = {name: make_engine(c) for name, c in creds.items()}
    ran: list[tuple[str, int]] = []

    def query(t: Any, sql: str, **params: Any) -> list[Any]:
        if t.source_name not in engines:
            return []
        with engines[t.source_name].connect() as c:
            rows = log.run(c, sql, params)
        ran.append((t.source_name, len(log.entries) - 1))
        return rows

    try:
        found = discover.discover(s, inp.cat, inp.mentioned, query, today)
    finally:
        for e in engines.values():
            e.dispose()
    _flush(s, log, ran, source_ids, None)
    old = {r.key: r for r in drafts.stored(s, "discovery")}
    for i, d in enumerate(found):
        body = {
            "type": d.type,
            "text": d.text,
            "evidence": d.evidence,
            "rules": [list(r) for r in d.rules],
        }
        row = old.pop(d.key, None)
        if row is None:
            s.add(
                SetupDraft(
                    kind="discovery",
                    key=d.key,
                    body=body,
                    origin={"by": "rules"},
                    confidence="high",
                    state="needs_confirmation",
                    position=i,
                )
            )
        elif not drafts.locked(row):
            row.body, row.position = body, i
    for row in old.values():
        if not drafts.locked(row):
            s.delete(row)
    s.flush()


def records_from_setup(s: Session) -> dict[tuple[str, str], dict[str, Any]]:
    """The non-metric records setup proposes: discovery rules (unless skipped) and choices."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in drafts.stored(s, "discovery"):
        if row.state != "skipped":
            for type_, key, body in row.body.get("rules", []):
                out[(type_, key)] = body
    for kind in RECORD_KINDS:
        for row in drafts.stored(s, kind):
            if row.state != "skipped":
                out[(kind, row.key)] = row.body
    return out


def _values(
    ctx: WorkerContext,
    s: Session,
    cid: uuid.UUID,
    inp: SetupInputs,
    creds: dict[str, Any],
    log: QueryLog,
    source_ids: dict[str, Any],
    company: dict[str, Any],
    today: dt.date,
) -> None:
    rows = [r for r in drafts.stored(s) if r.state not in ("skipped",)]
    metric = {("metric", r.key): r.body for r in rows if r.body.get("status") == "active"}
    spec, errors = compile_drafts(inp.base, {**records_from_setup(s), **metric}, inp.cat, company)
    duck = ctx.settings.analytics_path(cid)
    runner = Runner(spec, inp.cat, creds.get, log, duck if duck.exists() else None)
    try:
        for r in rows:
            # what the owner settled stays with the row through a recompute (M11c Decisions)
            settled = (r.value or {}).get("settled")
            _value(ctx, s, r, runner, errors, today, log, source_ids)
            if settled and r.value is not None:
                r.value = {**r.value, "settled": settled}
    finally:
        runner.close()
    s.flush()


def _value(
    ctx: WorkerContext,
    s: Session,
    r: SetupDraft,
    runner: Runner,
    errors: dict[str, str],
    today: dt.date,
    log: QueryLog,
    source_ids: dict[str, Any],
) -> None:
    """One draft's latest value, computed (never from a model)."""
    if r.body.get("status") != "active":
        r.value = None
        return
    if r.key in errors:
        r.value = {"state": "error", "note": errors[r.key]}
        if not drafts.locked(r):
            r.confidence, r.note = "check", _compile_note(errors[r.key])
        return
    n0 = len(runner.ran)
    try:
        c = runner.value(r.key, today)
        r.value = c.as_json()
    except NotRead:
        r.value = {"state": "not_read", "note": "Values appear after the sheet is read"}
    except Exception as e:
        r.value = {"state": "error", "note": type(e).__name__}
    _flush(s, log, runner.ran[n0:], source_ids, r.id)


def _compile_note(err: str) -> str:
    if "personal" in err.lower() or "excluded" in err.lower():
        return "Uses a table with personal data. It can be used without personal columns."
    # "net_revenue: breakdown reason isn't in the Ontology" → a sentence, no key or colon chain
    msg = err.split(": ", 1)[1] if re.match(r"^[a-z0-9_]+: ", err) else err
    msg = re.sub(r"\b(breakdown|column|table) ([a-z0-9_.]+)", r"the \1 “\2”", msg)
    return ("Couldn't compute this: " + msg[:1].lower() + msg[1:]).rstrip(".")[:299] + "."


def _conflicts(ctx: WorkerContext, s: Session, cid: uuid.UUID) -> None:
    """Net revenue vs an uploaded MIS column for the same month (decision: above 0.5%)."""
    resolved = s.scalars(
        sa.select(SetupDraft).where(
            SetupDraft.kind == "rule", SetupDraft.key == "revenue_book_of_record"
        )
    ).first()
    rows = [
        r
        for r in drafts.stored(s)
        if "revenue" in r.key and not drafts.locked(r) and (r.value or {}).get("state") == "ok"
    ]
    if resolved is not None or not rows:
        return
    row = next((r for r in rows if r.key == "net_revenue"), rows[0])
    ups = {
        u.filename: u
        for u in s.scalars(
            sa.select(Upload).where(Upload.deleted_at.is_(None), Upload.kind == "xlsx")
        )
    }
    if not ups:
        return
    inp_refs = gather_refs(s, ups)
    candidates = []
    for name, ref in inp_refs:
        try:
            raw = uploads.read_bytes(ctx.settings, ctx.key, ups[name])
        except OSError:
            continue
        candidates.append((name, ref, raw))
    v = row.value or {}
    period = dt.date.fromisoformat(v["period"]["start"])
    source = row.body.get("table", {}).get("source", "")
    found = conflicts.check(Decimal(v["value"]), period, source, candidates)
    if found is None:
        return
    row.confidence, row.note = "conflict", found["question"]
    row.value = {**v, "conflict": found}
    refs = list(row.refs or [])
    up = found["upload"]
    if not any(x.get("locator") == up["locator"] for x in refs):
        refs.insert(0, {"upload": up["file"], "locator": up["locator"], "text": up["header"]})
    row.refs = refs


def gather_refs(s: Session, ups: dict[str, Upload]) -> list[tuple[str, Any]]:
    from othor.setup.models import UploadRef
    from othor.setup.parse import Ref

    by_id = {u.id: n for n, u in ups.items()}
    out = []
    for r in s.scalars(
        sa.select(UploadRef).where(UploadRef.upload_id.in_(list(by_id)), UploadRef.kind == "column")
    ):
        if (r.data or {}).get("number"):
            out.append((by_id[r.upload_id], Ref(r.kind, r.locator, r.text, r.data or {})))
    return sorted(out, key=lambda x: (x[0], x[1].locator))
