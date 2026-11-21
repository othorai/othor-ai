# SPDX-License-Identifier: Apache-2.0
"""Onboarding steps 2, 3 and 5 (boards 2b, 2c, 2d, 2m, 2n, 2na) and the step 6 placeholder.

Uploads are read, drafts drafted and values computed by jobs in the worker. Nothing here writes
to the Ontology until Confirm (othor.setup.confirm). Every owner action writes an event row.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any
from urllib.parse import urlencode

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from othor.ai.gateway import ModelGateway, model_status, provider_for
from othor.ai.guards import numbers_grounded
from othor.core.events import record_event
from othor.core.localtime import local_date
from othor.core.models import SetupState
from othor.core.tenancy import tenant_scope
from othor.jobs.models import Job
from othor.jobs.queue import enqueue
from othor.ontology import service as ontology_service
from othor.ontology.compiler.catalog import load_catalog
from othor.setup import business, chat, confirm, connect, drafts, uploads
from othor.setup import conflicts as conflict_rules
from othor.setup.inputs import gather
from othor.setup.models import ChatMessage, ChatThread, SetupDraft, Upload
from othor.web import run_view
from othor.web import setup_view as v
from othor.web.context import services
from othor.web.routes.auth import STEP_ORDER, page
from othor.web.routes.pages import Owner, shell_ctx, signed_in
from othor.web.security import check_csrf

router = APIRouter(include_in_schema=False)
CSRF = [Depends(check_csrf)]
EXAMPLE = """\
About the business (an example; write yours in your own words)

We make ready-to-eat snacks for supermarkets in South India, with a small export trade to the Gulf.
Two plants, Hosur and Kochi; four product lines.

Every week we check dispatches, gross margin per kilo, the order book and receivables over 60 days.

This year's targets: net revenue, margin per kilo and DSO (write the numbers you use).

Revenue means invoice value net of returns and GST. Our MIS shows it with GST.

Unusual weeks that aren't problems: Onam and Diwali weeks, the plant shutdown in August, quarter-end
dispatch pushes.

Inputs and currencies: palm oil and potatoes drive costs; Gulf invoices are in AED.

Main customers: (names). Competitors: (names). Regulators: FSSAI.

Where the data lives: our ERP on MySQL, a PostgreSQL warehouse, the order tracker in Google Sheets.
"""


def _step(request: Request, o: Owner, step: str) -> Response | None:
    """Redirect when the owner isn't at (or past) this step, or setup is done."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        st = s.scalars(sa.select(SetupState)).first()
    cur = st.current_step if st else "company"
    if cur == "done":
        return RedirectResponse("/home", 303)
    if STEP_ORDER.index(step) > STEP_ORDER.index(cur):
        return RedirectResponse(f"/setup/{cur}", 303)
    return None


def _advance(s: Session, o: Owner, step: str) -> str:
    nxt = STEP_ORDER[STEP_ORDER.index(step) + 1]
    st = s.scalars(sa.select(SetupState)).first()
    if st is None:
        st = SetupState(current_step=step)
        s.add(st)
    before = st.current_step
    if before != "done" and STEP_ORDER.index(before) <= STEP_ORDER.index(step):
        st.current_step = nxt
        record_event(
            s,
            actor=str(o.viewer.id),
            action="onboarding.step_saved",
            object_type="setup_state",
            object_id=o.company.id,
            before={"step": before},
            after={"step": nxt},
        )
    return nxt


def _ctx(o: Owner, idx: int, **kw: Any) -> dict[str, Any]:
    return {**shell_ctx(o, "setup"), "step_index": idx, **kw}


def _now(request: Request) -> dt.datetime:
    return services(request).clock()


# ---------------------------------------------------------------------------------------------
# 2b / 2c · Upload


@router.get("/setup/upload", response_class=HTMLResponse)
def upload_page(request: Request, o: Owner = Depends(signed_in)) -> Response:
    if (r := _step(request, o, "upload")) is not None:
        return r
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        files = v.uploads_ctx(s)
    return page(request, "setup/upload.html", _ctx(o, 2, **files, error=None))


@router.get("/setup/upload/list", response_class=HTMLResponse)
def upload_list(request: Request, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        files = v.uploads_ctx(s)
    return page(request, "setup/_files.html", {**files, "csrf_token": request.state.csrf})


@router.post("/setup/upload/files", dependencies=CSRF)
async def upload_files(
    request: Request, files: list[UploadFile] = File(...), o: Owner = Depends(signed_in)
) -> Response:
    if (r := _step(request, o, "upload")) is not None:
        return r
    svc = services(request)
    error = None
    with svc.sf() as s, tenant_scope(o.company.id):
        for f in files:
            data = await f.read(uploads.MAX_BYTES + 1)
            try:
                up, created = uploads.add(
                    s,
                    svc.settings,
                    svc.key,
                    o.company.id,
                    filename=f.filename or "file",
                    data=data,
                    actor=str(o.viewer.id),
                )
            except uploads.UploadRefused as e:
                error = f"{f.filename}: {e}"
                continue
            if created:
                enqueue(s, "upload", {"upload_id": str(up.id)})
        s.commit()
        ctx = v.uploads_ctx(s)
    if request.headers.get("HX-Request"):
        return page(request, "setup/_files.html", {**ctx, "error": error})
    return page(request, "setup/upload.html", _ctx(o, 2, **ctx, error=error))


@router.post("/setup/upload/{uid}/remove", dependencies=CSRF)
def upload_remove(request: Request, uid: uuid.UUID, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        up = s.scalars(sa.select(Upload).where(Upload.id == uid)).first()
        if up is not None and up.deleted_at is None:
            up.deleted_at = svc.clock()
            record_event(
                s,
                actor=str(o.viewer.id),
                action="upload.removed",
                object_type="upload",
                object_id=up.id,
                before={"filename": up.filename},
            )
            s.commit()
    return RedirectResponse("/setup/upload", 303)


@router.post("/setup/upload/continue", dependencies=CSRF)
def upload_continue(request: Request, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        _advance(s, o, "upload")
        s.commit()
    return RedirectResponse("/setup/business", 303)


# ---------------------------------------------------------------------------------------------
# 2d · Business


EXAMPLE_BODY = EXAMPLE.split("\n", 2)[2].strip()  # the example without its title line


def _kick_business_draft(s: Session, request: Request) -> None:
    """Queue the business draft once every upload is read (the page and its poll both call it)."""
    svc = services(request)
    msg = business.message(s)
    available, _ = model_status(svc.settings, s, svc.clock())
    ups = v.uploads_ctx(s)
    if (
        available
        and not ups["reading"]
        and business.files_read(s) > 0
        and msg.draft_text is None
        and not v.pending(s, "business")
    ):
        enqueue(s, "business", {})


def _business_ctx(s: Session, request: Request, o: Owner) -> dict[str, Any]:
    """2d (M11c): the draft goes straight into the message box. `box` is the box's state:
    waiting (a file is still being read or the draft is being written), drafted, nofiles,
    nodraft (files, but no draft: the model is unavailable or found nothing)."""
    svc = services(request)
    msg = business.message(s)
    inp = gather(s)
    available, _ = model_status(svc.settings, s, svc.clock())
    seen = business.seen_in_files(inp.refs)
    chosen = msg.data_lives or []
    ups = v.uploads_ctx(s)
    drafting = available and v.pending(s, "business")
    has_files = bool(ups["files"])
    waiting = has_files and available and (ups["reading"] or drafting) and not msg.draft_text
    if waiting:
        box = "waiting"
    elif msg.draft_text:
        box = "drafted"
    elif not has_files:
        box = "nofiles"
    else:
        box = "nodraft"
    read_names = [f["name"] for f in ups["files"] if not f["reading"]]
    # owner, 20 Nov 2026: a ticking timer while files are read and the message is drafted, so the
    # box never looks stuck (the box polls itself every second); a calm note past 90 seconds
    elapsed, slow = "", False
    if box == "waiting":
        since = s.scalar(
            sa.select(sa.func.min(Job.created_at)).where(
                Job.kind.in_(("upload", "business")), Job.status.in_(("queued", "running"))
            )
        )
        if since is not None:
            now = svc.clock()
            if since.tzinfo is None:
                since = since.replace(tzinfo=dt.UTC)
            secs = max(int((now - since).total_seconds()), 0)
            elapsed, slow = f"{secs // 60}:{secs % 60:02d}", secs > 90
    return {
        "elapsed": elapsed,
        "slow": slow,
        "text": msg.text or (msg.draft_text or ""),
        "draft": msg.draft_text,
        "not_covered": msg.not_covered if msg.draft_text else None,
        "drafting": drafting,
        "box": box,
        "uploads": ups["files"],
        "drafted_from": read_names,
        "example": EXAMPLE_BODY,
        "model": available,
        "files_read": business.files_read(s),
        "data_lives": ", ".join(chosen),
        "seen": [x for x in seen if x not in chosen],
    }


@router.get("/setup/business", response_class=HTMLResponse)
def business_page(request: Request, o: Owner = Depends(signed_in)) -> Response:
    if (r := _step(request, o, "business")) is not None:
        return r
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        _kick_business_draft(s, request)
        s.commit()
        ctx = _business_ctx(s, request, o)
    return page(request, "setup/business.html", _ctx(o, 3, **ctx))


@router.get("/setup/business/draft", response_class=HTMLResponse)
def business_draft(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """The message box, polled while it waits; swaps in place when the draft lands."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        _kick_business_draft(s, request)
        s.commit()
        ctx = _business_ctx(s, request, o)
    return page(request, "setup/_business_box.html", {**ctx, "oob": True})


@router.get("/setup/business/example.txt")
def business_example(o: Owner = Depends(signed_in)) -> Response:
    return PlainTextResponse(
        EXAMPLE, headers={"Content-Disposition": 'attachment; filename="about-the-business.txt"'}
    )


@router.post("/setup/business/continue", dependencies=CSRF)
def business_continue(
    request: Request,
    text: str = Form(""),
    data_lives: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    if (r := _step(request, o, "business")) is not None:
        return r
    svc = services(request)
    places = [x.strip() for x in data_lives.replace(";", ",").split(",") if x.strip()]
    with svc.sf() as s, tenant_scope(o.company.id):
        business.save(s, text=text, data_lives=places, actor=str(o.viewer.id))
        _advance(s, o, "business")
        s.commit()
    return RedirectResponse("/setup/sources", 303)


def suggested_sources(s: Session) -> list[Any]:
    """10a / 6c cards (used by the Sources routes)."""
    return connect.suggested(s)


# ---------------------------------------------------------------------------------------------
# 2m / 2n · Metrics


def _enqueue_draft(s: Session, o: Owner, mode: str) -> None:
    meta = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "meta")).first()
    if meta is None:
        meta = SetupDraft(kind="meta", key="drafting", body={}, origin={"by": "system"})
        s.add(meta)
    meta.body = {**(meta.body or {}), "state": "queued", "mode": mode}
    enqueue(s, "draft", {"mode": mode, "actor": str(o.viewer.id)})


@router.get("/setup/metrics", response_class=HTMLResponse)
def metrics_page(request: Request, o: Owner = Depends(signed_in)) -> Response:
    if (r := _step(request, o, "metrics")) is not None:
        return r
    svc = services(request)
    q = request.query_params
    with svc.sf() as s, tenant_scope(o.company.id):
        meta = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "meta")).first()
        if meta is None:
            _enqueue_draft(s, o, "draft")
            s.commit()
        ctx = v.metrics_ctx(s, svc.settings, svc.clock())
        first = meta is None or meta.body.get("mode") == "draft"
        if ctx["drafting"] and first:
            return page(
                request, "setup/drafting.html", _ctx(o, 5, **v.progress_ctx(s, svc.clock()))
            )
        panel = _panel(s, request, o, q.get("panel"), q.get("key"), q.get("choice"))
        if panel is not None and panel["kind"] == "decisions":  # #32: one decision per screen
            panel["view"] = v.decisions_panel(ctx["checks"]["decisions"]["rows"], q.get("d"))
            if not panel["view"]["empty"]:  # Chat 1.3: the pick, and the chat behind Suggest edits
                available, _ = model_status(svc.settings, s, svc.clock())
                v.decision_chat(
                    s, panel["view"]["x"], editing=q.get("edit") == "1", model=available
                )
    return page(
        request,
        "setup/metrics.html",
        _ctx(
            o,
            5,
            **ctx,
            panel=panel,
            show_all=q.get("all") == "1",
            doc_error={
                "kind": "Upload a PDF, Word, Excel or Markdown file.",
                "size": "That file is too large or can't be stored.",
                "content": "This file isn’t the kind its name says.",
            }.get(q.get("doc_error") or ""),
        ),
    )


@router.get("/setup/metrics/progress", response_class=HTMLResponse)
def metrics_progress(request: Request, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        ctx = v.progress_ctx(s, svc.clock())
        if not confirm.drafting(s):
            return Response(status_code=200, headers={"HX-Redirect": "/setup/metrics"})
    return page(request, "setup/_progress.html", ctx)


@router.post("/setup/metrics/redraft", dependencies=CSRF)
def metrics_redraft(request: Request, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if not confirm.drafting(s):
            _enqueue_draft(s, o, "redraft")
            record_event(
                s,
                actor=str(o.viewer.id),
                action="setup.redraft_requested",
                object_type="setup_draft",
                object_id="metrics",
            )
            s.commit()
    return RedirectResponse("/setup/metrics", 303)


def _row(s: Session, key: str, kind: str = "metric") -> SetupDraft | None:
    return s.scalars(
        sa.select(SetupDraft).where(SetupDraft.kind == kind, SetupDraft.key == key)
    ).first()


@router.post("/setup/metrics/confirm", dependencies=CSRF)
@router.post("/setup/metrics/continue", dependencies=CSRF)
def metrics_confirm(request: Request, o: Owner = Depends(signed_in)) -> Response:
    if (r := _step(request, o, "metrics")) is not None:
        return r
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        try:
            confirm.confirm_setup(
                s,
                o.company,
                actor=str(o.viewer.id),
                today=svc.clock().date(),
                run_date=local_date(o.company, svc.clock()),
            )
        except (confirm.NotReady, ValueError):
            s.rollback()
            return RedirectResponse("/setup/metrics", 303)
        s.commit()
    return RedirectResponse("/setup/first-run", 303)


# --- panels: Choose (2na), the metric form, a discovery ----------------------------------------


@router.post("/setup/metrics/bring-back", dependencies=CSRF)
def metrics_bring_back(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """Undo a redraft's set-aside (the rule-made rows it didn't return)."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if drafts.bring_back(s, actor=str(o.viewer.id)):
            _enqueue_draft(s, o, "values")
        s.commit()
    return RedirectResponse("/setup/metrics", 303)


# --- 2ne / 2nf · redraft with your document ------------------------------------------------------

DOCUMENT_KINDS = ("pdf", "docx", "xlsx", "text")


@router.post("/setup/metrics/document", dependencies=CSRF)
async def metrics_document(
    request: Request, file: UploadFile = File(...), o: Owner = Depends(signed_in)
) -> Response:
    """Upload a data dictionary in place; the metrics are redrafted with it (2ne)."""
    if (r := _step(request, o, "metrics")) is not None:
        return r
    svc = services(request)
    name = file.filename or "document"
    from othor.setup import parse

    if parse.kind_of(name) not in DOCUMENT_KINDS:
        return RedirectResponse("/setup/metrics?doc_error=kind", 303)
    data = await file.read(uploads.MAX_BYTES + 1)
    with svc.sf() as s, tenant_scope(o.company.id):
        try:
            up, _ = uploads.add(
                s,
                svc.settings,
                svc.key,
                o.company.id,
                filename=name,
                data=data,
                actor=str(o.viewer.id),
            )
        except uploads.UploadRefused as e:
            from othor.setup import filecheck

            which = "content" if str(e) in (filecheck.MISMATCH, filecheck.NOT_UTF8) else "size"
            return RedirectResponse(f"/setup/metrics?doc_error={which}", 303)
        meta = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "meta")).first()
        if meta is not None:
            meta.body = {**(meta.body or {}), "state": "queued", "mode": "document"}
        enqueue(
            s, "draft", {"mode": "document", "upload_id": str(up.id), "actor": str(o.viewer.id)}
        )
        s.commit()
    return RedirectResponse("/setup/metrics/document", 303)


@router.get("/setup/metrics/document", response_class=HTMLResponse)
def metrics_redrafting(request: Request, o: Owner = Depends(signed_in)) -> Response:
    if (r := _step(request, o, "metrics")) is not None:
        return r
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        ctx = v.redraft_ctx(s)
    return page(request, "setup/redraft.html", _ctx(o, 5, **ctx))


def _panel(
    s: Session, request: Request, o: Owner, panel: str | None, key: str | None, choice: str | None
) -> dict[str, Any] | None:
    if panel in ("assumptions", "decisions", "confirm"):  # M11c: the three checks
        q = request.query_params
        return {
            "kind": panel,
            "key": key,
            "done": [k for k in (q.get("done") or "").split(",") if k],
        }
    if panel == "choose" and key:
        return _choose_ctx(s, request, key, choice)
    if panel == "chat" and key:
        purpose = request.query_params.get("purpose", "edit")
        row = _row(s, key)
        if row is not None and purpose in chat.PURPOSES:
            svc = services(request)
            available, _ = model_status(svc.settings, s, svc.clock())
            return v.chat_ctx(
                s,
                row,
                purpose,
                _replies(s, row, purpose),
                available,
                editing=request.query_params.get("edit") == "1",
            )
    if panel == "connect" and key:
        row = _row(s, key)
        if row is not None:
            sid = request.query_params.get("sid")
            return v.connect_ctx(
                s,
                services(request).settings,
                row,
                request.query_params.get("kind", "gsheets"),
                uuid.UUID(sid) if sid and re.fullmatch(r"[0-9a-f-]{36}", sid) else None,
            )
    if panel == "ontology":
        from othor.setup import ontology_draft

        return {"kind": "ontology", **ontology_draft.build(s)}
    if panel == "form":
        return _form_ctx(s, key)
    if panel == "found" and key:
        d = _row(s, key, "discovery")
        if d is not None:
            return {
                "kind": "found",
                "key": key,
                "type": d.body["type"].capitalize(),
                "text": d.body["text"],
                "skipped": d.state == "skipped",
            }
    return None


def _gateway(request: Request) -> ModelGateway:
    svc = services(request)
    return ModelGateway(
        svc.sf,
        provider_for(svc.settings),
        clock=svc.clock,
        monthly_tokens=svc.settings.ai_monthly_tokens,
    )


def _choose_ctx(
    s: Session, request: Request, key: str, choice: str | None
) -> dict[str, Any] | None:
    row = _row(s, key)
    c = (row.value or {}).get("conflict") if row else None
    if row is None or c is None:
        return None
    svc = services(request)
    available, _ = model_status(svc.settings, s, svc.clock())
    unit = row.body.get("unit", "inr")
    options = [{**x, "shown": v.value_text(x["value"], unit)} for x in c["options"]]
    thread = s.scalars(
        sa.select(ChatThread).where(
            ChatThread.subject == f"metric:{key}", ChatThread.status == "open"
        )
    ).first()
    messages = []
    if thread is not None:
        messages = [
            {"role": m.role, "text": m.text, "choice": (m.proposal or {}).get("choice")}
            for m in s.scalars(
                sa.select(ChatMessage)
                .where(ChatMessage.thread_id == thread.id)
                .order_by(ChatMessage.created_at)
            )
        ]
        choice = choice or next((m["choice"] for m in reversed(messages) if m["choice"]), None)
    # Chat 1.1 (owner, 18 Nov 2026): Othor AI's pick by default: the database, the book of
    # record (code's rule, the same for every company); the owner changes it with one click
    ids = [str(x.get("id")) for x in options]
    by_ai = choice is None
    if by_ai:
        choice = "source" if "source" in ids else (ids[0] if ids else None)
    proposal = None
    if choice in ("source", "upload", "both"):
        cat = load_catalog(s)
        t = row.body.get("table") or {}
        info = cat.tables.get((t.get("source", ""), t.get("schema", ""), t.get("table", "")))
        cols = {x.lower() for x in info.columns} if info else set()
        _, _, text = conflict_rules.resolution(c, choice, row.body, cols)
        proposal = {"choice": choice, "text": text, "name": row.body.get("name")}
    return {
        "kind": "choose",
        "key": key,
        "name": row.body.get("name"),
        "month": c["month"],
        "question": c["question"],
        "options": options,
        "messages": messages,
        "proposal": proposal,
        "by_ai": by_ai and proposal is not None,
        "editing": request.query_params.get("edit") == "1"
        or any(m["role"] == "user" for m in messages),
        "edit_url": f"/setup/metrics?panel=choose&key={key}&edit=1",
        "model": available,
        "not_available": v.NOT_AVAILABLE,
    }


@router.post("/setup/metrics/{key}/choose", dependencies=CSRF)
def metric_choose(
    request: Request, key: str, choice: str = Form(...), o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        row = _row(s, key)
        if row is None or confirm.drafting(s):
            return RedirectResponse("/setup/metrics", 303)
        try:
            confirm.choose(s, row, choice, actor=str(o.viewer.id))
        except ValueError:
            return RedirectResponse("/setup/metrics", 303)
        for t in s.scalars(sa.select(ChatThread).where(ChatThread.subject == f"metric:{key}")):
            t.status = "applied"
        _enqueue_draft(s, o, "values")
        s.commit()
    return RedirectResponse("/setup/metrics", 303)


@router.post("/setup/metrics/{key}/chat", dependencies=CSRF)
def metric_chat(
    request: Request,
    key: str,
    text: str = Form(""),
    back: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    """The conflict chat with Claude. Without the model the composer is off; Choose still works.
    Chat 1.3: from the Decisions panel's Suggest edits it lands back on that decision."""
    svc = services(request)
    url = f"/setup/metrics?panel=choose&key={key}"
    if back.startswith("/setup/metrics?panel=decisions&") and "//" not in back:
        url = back
    with svc.sf() as s, tenant_scope(o.company.id):
        row = _row(s, key)
        c = (row.value or {}).get("conflict") if row else None
        available, _ = model_status(svc.settings, s, svc.clock())
        if row is None or c is None or not available or not text.strip():
            return RedirectResponse(url, 303)
        thread = s.scalars(
            sa.select(ChatThread).where(
                ChatThread.subject == f"metric:{key}", ChatThread.status == "open"
            )
        ).first()
        if thread is None:
            thread = ChatThread(purpose="conflict", subject=f"metric:{key}")
            s.add(thread)
            s.flush()
        history = [
            {"role": m.role, "text": m.text}
            for m in s.scalars(
                sa.select(ChatMessage)
                .where(ChatMessage.thread_id == thread.id)
                .order_by(ChatMessage.created_at)
            )
        ]
        s.add(ChatMessage(thread_id=thread.id, role="user", text=text.strip()[:2000]))
        s.commit()
        thread_id = thread.id
        unit = row.body.get("unit", "inr")
        inputs = {
            "metric": row.body.get("name"),
            "question": c["question"],
            "month": c["month"],
            "options": [
                {
                    "id": x["id"],
                    "label": x["label"],
                    "where": x["where"],
                    "value": v.value_text(x["value"], unit),
                }
                for x in c["options"]
            ],
            "conversation": [*history, {"role": "user", "text": text.strip()[:2000]}],
        }
    res = _gateway(request).complete(o.company.id, "conflict_chat", "v1", inputs)
    with svc.sf() as s, tenant_scope(o.company.id):
        if res.ok and res.output:
            reply = str(res.output.get("text") or "").strip()
            if not reply or not numbers_grounded(reply, inputs):
                reply = "Which should every brief use?"
            ch = res.output.get("choice") or None
            s.add(
                ChatMessage(
                    thread_id=thread_id,
                    role="ai",
                    text=reply[:2000],
                    proposal={"choice": ch} if ch else None,
                )
            )
        else:
            s.add(ChatMessage(thread_id=thread_id, role="system", text=v.NOT_AVAILABLE))
        s.commit()
    return RedirectResponse(url, 303)


def _form_can_show(b: dict[str, Any]) -> bool:
    """The form holds one column, one date, and filters of a column against a value. Anything
    more (a CASE measure, a snapshot, a derived formula) is never flattened by a save."""
    m = b.get("measure") or {}
    if b.get("formula") or b.get("kind") == "snapshot" or not b.get("table"):
        return False
    if m.get("expr") is not None and set(m["expr"]) != {"col"}:
        return False
    filters = b.get("filters") or []
    if len(filters) > 1:
        return False
    for f in filters:
        args = f.get("args") or []
        if len(args) != 2 or "col" not in args[0] or "lit" not in args[1]:
            return False
    return True


AGGS = [
    ("sum", "Sum"),
    ("count", "Count of rows"),
    ("avg", "Average"),
    ("count_distinct", "Distinct count"),
]
GRAINS = [("day", "Daily"), ("week", "Weekly"), ("month", "Monthly")]


def _form_ctx(
    s: Session, key: str | None, error: str | None = None, values: dict[str, Any] | None = None
) -> dict[str, Any]:
    cat = load_catalog(s)
    tables = [
        (f"{t.source_name}|{t.schema}|{t.name}", f"{t.source_name} · {t.schema}.{t.name}")
        for t in sorted(cat.tables.values(), key=lambda t: (t.source_name, t.schema, t.name))
        if t.use == "metric_source"
    ]
    vals = values or {}
    row = _row(s, key) if key else None
    simple = row is None or _form_can_show(row.body)
    if row is not None and not values and simple:
        b = row.body
        t = b.get("table") or {}
        m = b.get("measure") or {}
        f = (b.get("filters") or [None])[0]
        vals = {
            "name": b.get("name", ""),
            "table": f"{t.get('source')}|{t.get('schema')}|{t.get('table')}" if t else "",
            "agg": m.get("agg", "sum"),
            "column": (m.get("expr") or {}).get("col", ""),
            "date": b.get("date", ""),
            "grain": b.get("grain", "month"),
            "breakdowns": ", ".join(b.get("breakdowns", [])),
            "filter_column": f["args"][0].get("col", "") if f and f.get("op") else "",
            "filter_op": f.get("op", "=") if f else "=",
            "filter_value": f["args"][1].get("lit", "") if f and f.get("op") else "",
        }
    sel = vals.get("table") or (tables[0][0] if tables else "")
    cols: list[tuple[str, str]] = []
    dates: list[tuple[str, str]] = [("", "—")]
    if sel:
        src, schema, name = [*sel.split("|"), "", "", ""][:3]
        info = cat.tables.get((src, schema, name))
        if info is not None:
            for c in info.columns.values():
                if c.personal:
                    continue
                if c.kind in ("date", "timestamp"):
                    dates.append((c.name, c.name))
                else:
                    cols.append((c.name, c.name))
    return {
        "kind": "form",
        "key": key,
        "editing": row is not None,
        "simple": simple,
        "tables": tables,
        "aggs": AGGS,
        "grains": GRAINS,
        "cols": [("", "—"), *cols],
        "dates": dates,
        "vals": {**vals, "table": sel},
        "error": error,
    }


@router.get("/setup/metrics/form-columns", response_class=HTMLResponse)
def form_columns(
    request: Request, table: str = "", key: str = "", o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        ctx = _form_ctx(s, key or None, values={"table": table})
    return page(request, "setup/_form_columns.html", {"panel": ctx})


@router.post("/setup/metrics/form", dependencies=CSRF)
async def metric_form(request: Request, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    form = await request.form()
    vals = {
        k: str(form.get(k) or "")
        for k in (
            "key",
            "name",
            "table",
            "agg",
            "column",
            "date",
            "grain",
            "breakdowns",
            "filter_column",
            "filter_op",
            "filter_value",
            "unit",
        )
    }
    src, schema, name = [*vals["table"].split("|"), "", "", ""][:3]
    payload: dict[str, Any] = {
        **vals,
        "source": src,
        "schema": schema,
        "table": name,
        "breakdowns": [x.strip() for x in vals["breakdowns"].split(",") if x.strip()],
    }
    with svc.sf() as s, tenant_scope(o.company.id):
        cat = load_catalog(s)
        try:
            body = drafts.owner_metric(payload, cat)
            if (
                not vals["key"]
                and confirm.in_use(drafts.stored(s)) >= ontology_service.max_metrics()
            ):  # the ceiling (M15.1): a new metric past it is refused here, not at Confirm
                raise ValueError(ontology_service.ceiling_text("Skip"))
        except ValueError as e:
            ctx = v.metrics_ctx(s, svc.settings, svc.clock())
            panel = _form_ctx(
                s, vals["key"] or None, error=str(e).splitlines()[0][:200], values=vals
            )
            return page(
                request,
                "setup/metrics.html",
                _ctx(o, 5, **ctx, panel=panel, show_all=True),
                status=422,
            )
        key = vals["key"] or drafts.slug(body["name"])
        row = _row(s, key)
        if row is not None and vals["key"] and not _form_can_show(row.body):
            return RedirectResponse(f"/setup/metrics?panel=form&key={key}", 303)
        before = row.body if row else None
        if row is None:
            row = SetupDraft(kind="metric", key=key, body=body, position=999)
            s.add(row)
        row.body, row.prev_body, row.changed = body, None, False
        row.origin = {"by": "owner", "rule": "Added by you"}
        row.refs = row.refs or []
        row.confidence, row.state, row.note, row.value = "high", "confirmed", None, None
        row.frozen = True
        s.flush()
        record_event(
            s,
            actor=str(o.viewer.id),
            action="setup.metric_saved",
            object_type="setup_draft",
            object_id=row.id,
            before=before,
            after=body,
        )
        _enqueue_draft(s, o, "values")
        s.commit()
    return RedirectResponse("/setup/metrics", 303)


@router.post("/setup/metrics/found/{key}/{action}", dependencies=CSRF)
def found_action(
    request: Request,
    key: str,
    action: str,
    use: str = Form(""),
    next_: str = Form("", alias="next"),
    o: Owner = Depends(signed_in),
) -> Response:
    """Keep or drop one assumption (2n "Change", M11c m6: keep or drop only)."""
    if action == "set":  # the panel's radio: use=keep|skip
        action = use
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        d = _row(s, key, "discovery")
        if d is not None and action in ("keep", "skip") and not confirm.drafting(s):
            before = d.state
            after = "skipped" if action == "skip" else "needs_confirmation"
            if after != before:
                d.state = after
                record_event(
                    s,
                    actor=str(o.viewer.id),
                    action=f"setup.discovery_{action}",
                    object_type="setup_draft",
                    object_id=d.id,
                    before={"state": before},
                    after={"state": d.state},
                )
                _enqueue_draft(s, o, "values")
            s.commit()
    return RedirectResponse(_next(next_), 303)


def _next(url: str) -> str:
    """Where a Metrics action lands: the panel it came from, else the page."""
    return url if url.startswith("/setup/metrics") and "//" not in url else "/setup/metrics"


@router.post("/setup/metrics/decide", dependencies=CSRF)
async def metrics_decide(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """One decision's answer (#32, one per screen): a conflict's pick (choice:<key>) or a
    redrafted definition accepted or kept (changed:<key>). Nothing is preselected; with nothing
    picked it stays. Saved at once (one event), then the panel moves on to the next decision
    still open, or closes when none is left."""
    form = await request.form()
    key = str(form.get("d") or "")
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if confirm.drafting(s):
            return RedirectResponse(v.decision_url(key), 303)
        n = 0
        for name, value in form.multi_items():
            kind, _, k = str(name).partition(":")
            row = _row(s, k) if k else None
            if row is None:
                continue
            if kind == "choice" and str(value) in ("source", "upload", "both"):
                try:
                    confirm.choose(s, row, str(value), actor=str(o.viewer.id))
                except ValueError:
                    continue
                for th in s.scalars(
                    sa.select(ChatThread).where(ChatThread.subject == f"metric:{k}")
                ):
                    th.status = "applied"
                n += 1
            elif kind == "changed" and str(value) in ("accept", "reject") and row.changed:
                drafts.accept_change(s, row, actor=str(o.viewer.id), accept=value == "accept")
                n += 1
            key = key or k
        if not n:
            s.commit()
            return RedirectResponse(v.decision_url(key), 303)
        _enqueue_draft(s, o, "values")
        s.flush()
        url = v.decision_after(v.decisions_of(s, drafts.stored(s))[0], key)
        s.commit()
    return RedirectResponse(url, 303)


@router.post("/setup/metrics/confirm-all", dependencies=CSRF)
async def metrics_confirm_all(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """ "Confirm the N remaining" (M11c m7): one request, one event per metric; the toast's
    Undo puts them back."""
    form = await request.form()
    keys = [str(k) for k in form.getlist("key")]
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if confirm.drafting(s):
            return RedirectResponse("/setup/metrics?panel=confirm", 303)
        rows = [r for r in drafts.stored(s) if r.key in keys]
        done = drafts.confirm_rows(s, rows, actor=str(o.viewer.id))
        s.commit()
    q = urlencode({"panel": "confirm", "done": ",".join(done)}) if done else "panel=confirm"
    return RedirectResponse(f"/setup/metrics?{q}", 303)


@router.post("/setup/metrics/unconfirm", dependencies=CSRF)
async def metrics_unconfirm(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """Undo Confirm for one metric or a confirm-all batch (M11c m8)."""
    form = await request.form()
    keys = {str(k) for k in form.getlist("key")}
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        for r in drafts.stored(s):
            if r.key in keys:
                drafts.unconfirm_row(s, r, actor=str(o.viewer.id))
        s.commit()
    return RedirectResponse(_next(str(form.get("next") or "/setup/metrics?panel=confirm")), 303)


def _replies(s: Session, row: SetupDraft, purpose: str) -> list[Any]:
    from othor.jobs.draft_job import records_from_setup

    inp = gather(s)
    sugg = suggested_sources(s) if purpose == "connect" else None
    return chat.quick_replies(row, purpose, inp.cat, {**inp.records, **records_from_setup(s)}, sugg)


def _form_patch(form: Any) -> Any:
    """A quick reply's small form → a typed patch."""
    from othor.setup.patches import Patch

    kind = str(form.get("patch") or "")
    if kind == "set_threshold":
        try:
            return Patch(kind, {"value": int(str(form.get("value") or "0"))})
        except ValueError:
            return None
    if kind == "exclude":
        values = [x.strip() for x in str(form.get("values") or "").split(",") if x.strip()]
        return Patch(kind, {"breakdown": str(form.get("breakdown") or ""), "values": values})
    if kind == "set_breakdowns":
        return Patch(kind, {"breakdowns": [str(x) for x in form.getlist("breakdowns")]})
    return None


@router.post("/setup/metrics/{key}/chat/{purpose}", dependencies=CSRF)
async def chat_turn(
    request: Request, key: str, purpose: str, o: Owner = Depends(signed_in)
) -> Response:
    """One owner turn: a quick reply (a patch or an action), a small form, or typed text."""
    svc = services(request)
    form = await request.form()
    url, done = _chat_back(form, v.chat_url(key, purpose))
    if purpose not in chat.PURPOSES:
        return RedirectResponse(done, 303)
    with svc.sf() as s, tenant_scope(o.company.id):
        row = _row(s, key)
        if row is None or confirm.drafting(s):
            return RedirectResponse(done, 303)
        actor = str(o.viewer.id)
        patch, text = None, str(form.get("text") or "").strip()
        if form.get("reply") is not None:
            replies = _replies(s, row, purpose)
            try:
                q = replies[int(str(form.get("reply")))]
            except (ValueError, IndexError):
                return RedirectResponse(url, 303)
            if q.action in ("skip", "needs_source"):
                chat.act(s, row, q.action, actor=actor)
                s.commit()
                return RedirectResponse(done, 303)
            if q.action == "form_panel":
                return RedirectResponse(f"/setup/metrics?panel=form&key={key}", 303)
            if q.action and q.action.startswith("connect:"):
                return RedirectResponse(
                    f"/setup/metrics?panel=connect&key={key}&kind={q.action[8:]}", 303
                )
            if q.action == "form":
                patch = _form_patch(form)
                if patch is None:
                    return RedirectResponse(url, 303)
                given = [
                    ", ".join(str(x) for x in form.getlist(f["name"]) if str(x).strip())
                    for f in q.inputs
                ]
                text = " · ".join([q.label, *[g for g in given if g]])
            elif q.patch is not None:
                patch, text = q.patch, q.label
            else:
                return RedirectResponse(url + "&ask=1", 303)
        if patch is None and not text:
            return RedirectResponse(url, 303)
        thread = chat.thread_for(s, key, purpose)
        assert thread is not None
        m = chat.add_turn(s, thread, text=text, patch=patch, actor=actor)
        # Chat 1.1: "Apply my pick" drafts it (its value computed by SQL) and applies it in one
        # click; the owner's own button press is the apply (non-negotiable 6)
        then_apply = form.get("apply") == "1" and patch is not None
        enqueue(
            s,
            "chat",
            {"message_id": str(m.id), **({"apply": True, "actor": actor} if then_apply else {})},
        )
        s.commit()
    return RedirectResponse(url, 303)


def _chat_back(form: Any, url: str) -> tuple[str, str]:
    """(this chat's page, where to go when it's done): Metrics, or the Sources review (6ha)."""
    back, close = str(form.get("back") or ""), str(form.get("close") or "")
    if back.startswith("/sources?") and close.startswith("/sources"):
        return back, close
    return url, "/setup/metrics"


@router.post("/setup/metrics/{key}/chat/{purpose}/apply/{mid}", dependencies=CSRF)
async def chat_apply(
    request: Request, key: str, purpose: str, mid: uuid.UUID, o: Owner = Depends(signed_in)
) -> Response:
    """Apply a card. Before Confirm the draft changes; after Confirm a live metric's change is
    written as a new Ontology version (a draft from a new source waits for Confirm N metrics)."""
    from othor.core.models import Company

    svc = services(request)
    _, done = _chat_back(await request.form(), "")
    with svc.sf() as s, tenant_scope(o.company.id):
        row = _row(s, key)
        m = s.scalars(sa.select(ChatMessage).where(ChatMessage.id == mid)).first()
        card = (m.proposal or {}).get("card") if m else None
        if row is None or m is None or card is None or m.applied or confirm.drafting(s):
            return RedirectResponse(done, 303)
        company = s.scalars(sa.select(Company).where(Company.id == o.company.id)).one()
        chat.apply_message(s, company, row, m, actor=str(o.viewer.id), today=svc.clock().date())
        s.commit()
    return RedirectResponse(done, 303)


@router.post("/setup/metrics/{key}/without-personal", dependencies=CSRF)
def without_personal(
    request: Request,
    key: str,
    next_: str = Form("", alias="next"),
    o: Owner = Depends(signed_in),
) -> Response:
    """2m/2n: use an excluded table without its personal columns, then redraft that metric.
    From the Decisions panel it moves on to the next decision (#32)."""
    from othor.connectors import service as source_service
    from othor.connectors.models import SourceTable

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        row = _row(s, key)
        if row is None or row.state != "excluded_table":
            return RedirectResponse("/setup/metrics", 303)
        m = re.match(r"([\w.]+)\.(\w+) has personal data", row.note or "")
        if m:
            t = s.scalars(
                sa.select(SourceTable).where(
                    SourceTable.schema == m.group(1), SourceTable.name == m.group(2)
                )
            ).first()
            if t is not None:
                source_service.use_without_personal(
                    s, t, "metric_source", actor=str(o.viewer.id), now=svc.clock()
                )
        row.state, row.note = "needs_source", "Usable without personal columns; drafting again."
        _enqueue_draft(s, o, "redraft")
        s.flush()
        url = _after_decision(s, key, next_) or "/setup/metrics"
        s.commit()
    return RedirectResponse(url, 303)


def _after_decision(s: Session, key: str, next_: str) -> str | None:
    """An action from the Decisions panel moves on to the next open decision (#32)."""
    if not next_.startswith(DECISIONS):
        return None
    return v.decision_after(v.decisions_of(s, drafts.stored(s))[0], key)


DECISIONS = "/setup/metrics?panel=decisions"


# the generic row actions last: they must not shadow choose, chat or form


@router.post("/setup/metrics/{key}/{action}", dependencies=CSRF)
def metric_action(
    request: Request,
    key: str,
    action: str,
    next_: str = Form("", alias="next"),
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        row = _row(s, key)
        # Set aside is a state change only: it doesn't wait on a values job (#32: settle the
        # conflict, then set the next one aside while its values compute)
        if row is None or (confirm.drafting(s) and action != "skip"):
            return RedirectResponse("/setup/metrics", 303)
        recompute = False
        if action == "confirm" and row.confidence != "conflict":
            drafts.confirm_row(s, row, actor=str(o.viewer.id))
        elif action == "reopen":  # Change on a decided item (#32): it opens again
            recompute = confirm.reopen(s, row, actor=str(o.viewer.id))
            next_ = v.decision_url(key)
        elif action == "skip":
            before = row.state
            if before in ("needs_source", "excluded_table"):  # Change brings it back as it was
                row.origin = {**(row.origin or {}), "skipped_from": before}
            row.state = "skipped"
            record_event(
                s,
                actor=str(o.viewer.id),
                action="setup.draft_skipped",
                object_type="setup_draft",
                object_id=row.id,
                before={"state": before},
                after={"state": "skipped"},
            )
        elif action in ("accept", "reject"):
            drafts.accept_change(s, row, actor=str(o.viewer.id), accept=action == "accept")
            recompute = action == "reject"
        if recompute:
            _enqueue_draft(s, o, "values")
        s.flush()
        url = (_after_decision(s, key, next_) if action == "skip" else None) or _next(next_)
        s.commit()
    return RedirectResponse(url, 303)


# ---------------------------------------------------------------------------------------------
# 2o · First run: dry run #0001 after Confirm


def _first_run_view(request: Request, o: Owner) -> Any:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        return run_view.first_run(s, svc.settings, o.company, svc.clock())


@router.get("/setup/first-run", response_class=HTMLResponse)
def first_run(request: Request, o: Owner = Depends(signed_in)) -> Response:
    if (r := _step(request, o, "first-run")) is not None:
        return r
    return page(
        request,
        "setup/first_run.html",
        _ctx(o, 6, view=_first_run_view(request, o), levels=_levels(request, o)),
    )


def _levels(request: Request, o: Owner) -> dict[str, Any] | None:
    """2.4 L5: onboarding ends on the Business Model's level, the next items offered (the level 1
    items still missing first, then level 2), each one sentence in the chat."""
    from othor.web.ontology_view import level_view

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if not ontology_service.confirmed(s):
            return None
        view = level_view(s, svc.settings, upto=2)  # level 3 waits for the app
    return {**view, "open": bool(view["left"])}


@router.get("/setup/first-run/progress", response_class=HTMLResponse)
def first_run_progress(request: Request, o: Owner = Depends(signed_in)) -> Response:
    view = _first_run_view(request, o)
    if view is None:
        return HTMLResponse("")
    return page(request, "setup/_first_run.html", _ctx(o, 6, view=view))


@router.post("/setup/first-run/retry", dependencies=CSRF)
def first_run_retry(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """Run again: the same job and run, resumed from the step that stopped."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        job = s.scalars(sa.select(Job).where(Job.dedupe_key == confirm.FIRST_RUN_KEY)).first()
        if job is not None and job.status in ("failed", "running"):
            before = job.status
            job.status, job.attempts, job.run_after, job.lease_until = "queued", 0, None, None
            record_event(
                s,
                actor=o.viewer.id,
                action="run.retried",
                object_type="job",
                object_id=job.id,
                before={"status": before},
                after={"status": "queued"},
            )
            s.commit()
    # from Morning Brief (the owner left setup before the brief was written) it stays there
    ref = request.headers.get("referer") or ""
    return RedirectResponse("/brief" if "/brief" in ref else "/setup/first-run", 303)


# --- M11c · the first-run strip: the owner went to the app before the first brief was written ---

FIRST_RUN_COOKIE = "othor_first_run"


@router.get("/first-run/strip", response_class=HTMLResponse)
def first_run_strip(request: Request, o: Owner = Depends(signed_in)) -> Response:
    view = _first_run_view(request, o)
    if view is None:
        r = HTMLResponse("")
        r.delete_cookie(FIRST_RUN_COOKIE)
        return r
    return page(request, "setup/_strip.html", {**shell_ctx(o, ""), "view": view})


@router.get("/first-run/open")
def first_run_open(request: Request, close: str = "", o: Owner = Depends(signed_in)) -> Response:
    """Open (or close) the strip: it goes; Open lands on Morning Brief."""
    from othor.web.context import local_path

    back = local_path(request.headers.get("referer"), "/home", request.url.netloc)
    to = "/brief" if not close else back
    r = RedirectResponse(to, 303)
    r.delete_cookie(FIRST_RUN_COOKIE)
    return r
