# SPDX-License-Identifier: Apache-2.0
"""Answer one chat turn (M4b): Claude's reply and patch for typed text, or the quick reply's patch;
then the draft card with today's value computed by SQL (read-only, run_query purpose "draft").

payload: {"message_id": str}
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa

from othor.ai.gateway import model_status
from othor.connectors.models import Source
from othor.connectors.sql.query_log import QueryLog
from othor.core import actors
from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext
from othor.ontology.compiler.api import company_settings
from othor.setup import chat
from othor.setup.compute import NotRead, Runner, compile_drafts
from othor.setup.inputs import creds_loader, gather
from othor.setup.models import ChatMessage, ChatThread, SetupDraft
from othor.setup.patches import Patch, PatchRefused


def run(ctx: WorkerContext, job: Job) -> None:
    cid = job.company_id
    mid = uuid.UUID(job.payload["message_id"])
    with ctx.sf() as s, tenant_scope(cid):
        msg = s.scalars(sa.select(ChatMessage).where(ChatMessage.id == mid)).one()
        thread = s.scalars(sa.select(ChatThread).where(ChatThread.id == msg.thread_id)).one()
        if thread.subject.startswith("tab:"):
            s.expunge_all()
            _tab_fix(ctx, cid, mid, thread)
            return
        key = thread.subject.split(":", 1)[1]
        row = chat.find_row(s, key)
        if row is None:
            return
        inp = gather(s)
        from othor.jobs.draft_job import records_from_setup

        setup_records = records_from_setup(s)
        records = {**inp.records, **setup_records}
        history = chat.messages(s, thread)
        replies = chat.quick_replies(row, thread.purpose, inp.cat, records)
        available, _ = model_status(ctx.settings, s, ctx.clock())
        sent: dict[str, Any] = msg.proposal or {}
        patch = Patch.from_json(sent["patch"]) if sent.get("patch") else None
        row_body, row_value, row_id = dict(row.body), row.value, row.id
        company = company_settings(s.scalars(sa.select(Company).where(Company.id == cid)).one())
        metrics = {
            ("metric", r.key): r.body
            for r in s.scalars(
                sa.select(SetupDraft).where(
                    SetupDraft.kind == "metric", SetupDraft.state != "skipped"
                )
            )
            if r.body.get("status") == "active"
        }
        creds = creds_loader(s, ctx.key, cid)
        source_ids = {x.name: x.id for x in s.scalars(sa.select(Source))}
        personal = inp.personal

    text = None
    if patch is None:  # typed text: Claude, if available
        inputs = chat.model_inputs(row, thread.purpose, inp.cat, history, replies)
        if available and ctx.gateway is not None:
            res = ctx.gateway.complete(
                cid, chat.PURPOSES.get(thread.purpose, "metric_edit"), "v1", inputs
            )
            if res.ok and res.output:
                text = chat.reply_text(res.output, inputs)
                patch = chat.proposal_from_model(res.output, inputs)
        if text is None:
            with ctx.sf() as s, tenant_scope(cid):
                s.add(ChatMessage(thread_id=thread.id, role="system", text=chat.NOT_AVAILABLE))
                _done(s, mid)
                s.commit()
            return

    card: dict[str, Any] | None = None
    note = None
    if patch is not None:
        try:
            c = chat.card_for(row, patch, inp.cat, records)
            card = c.as_json()
        except PatchRefused as e:
            note = f"That change can’t be made: {e}."
    value = None
    if card is not None:
        drafts_ = {
            **setup_records,
            **metrics,
            ("metric", key): card["body"],
            **{(t, k): b for t, k, b in card["records"]},
        }
        spec, errors = compile_drafts(inp.base, drafts_, inp.cat, company)
        if key in errors:
            note, card = f"That change can’t be computed: {errors[key]}.", None
        else:
            duck = ctx.settings.analytics_path(cid)
            log = QueryLog(personal=personal)
            runner = Runner(spec, inp.cat, creds.get, log, duck if duck.exists() else None)
            try:
                value = runner.value(key, ctx.clock().date()).as_json()
            except NotRead:
                value = {"state": "not_read"}
            except Exception as e:
                value = {"state": "error", "note": type(e).__name__}
            finally:
                runner.close()
            from othor.jobs.draft_job import _flush

            with ctx.sf() as s, tenant_scope(cid):
                _flush(s, log, runner.ran, source_ids, row_id)
                s.commit()

    with ctx.sf() as s, tenant_scope(cid):
        proposal = None
        if card is not None:
            proposal = {
                "patch": patch.as_json() if patch else None,
                "card": card,
                "value": value,
                "before": row_value,
                "before_unit": row_body.get("unit"),
            }
        reply = ChatMessage(
            thread_id=thread.id,
            role="ai",
            text=note or text or "Here is the change and what it does today.",
            proposal=proposal,
        )
        s.add(reply)
        s.flush()
        # Chat 1.1: the owner pressed "Apply my pick": applied once its value is computed (a
        # change that can't be computed stays a note, nothing applied)
        if job.payload.get("apply") and proposal is not None:
            row_now = chat.find_row(s, key)
            co = s.scalars(sa.select(Company).where(Company.id == cid)).one()
            if row_now is not None:
                chat.apply_message(
                    s,
                    co,
                    row_now,
                    reply,
                    # 0.35: Othor AI applied it on the person's request
                    actor=actors.othor_ai(job.payload.get("actor")),
                    today=ctx.clock().date(),
                )
        _done(s, mid)
        s.commit()


def _done(s: Any, mid: uuid.UUID) -> None:
    m = s.scalars(sa.select(ChatMessage).where(ChatMessage.id == mid)).one()
    m.proposal = {**(m.proposal or {}), "state": "answered"}


def _tab_fix(ctx: WorkerContext, cid: uuid.UUID, mid: uuid.UUID, thread: ChatThread) -> None:
    """10c: the owner's line → a typed shape change (checked), or "Not available right now"."""
    from othor.connectors.models import SheetTab
    from othor.setup import tab_fix

    with ctx.sf() as s, tenant_scope(cid):
        tab = s.scalars(
            sa.select(SheetTab).where(SheetTab.id == uuid.UUID(thread.subject[4:]))
        ).one()
        history = chat.messages(s, thread)
        inputs = tab_fix.model_inputs(tab, history)
        available, _ = model_status(ctx.settings, s, ctx.clock())
        s.expunge(tab)
    text, prop = None, None
    if available and ctx.gateway is not None:
        res = ctx.gateway.complete(cid, tab_fix.PURPOSE, "v1", inputs)
        if res.ok and res.output:
            text = chat.reply_text(res.output, inputs)
            prop = tab_fix.proposal(res.output, tab, inputs)
    with ctx.sf() as s, tenant_scope(cid):
        if text is None:
            s.add(ChatMessage(thread_id=thread.id, role="system", text=chat.NOT_AVAILABLE))
        else:
            s.add(
                ChatMessage(
                    thread_id=thread.id,
                    role="ai",
                    text=text if prop else "I couldn't turn that into a fix. Try the form.",
                    proposal=prop,
                )
            )
        _done(s, mid)
        s.commit()
