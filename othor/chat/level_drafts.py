# SPDX-License-Identifier: Apache-2.0
"""Business Model 2.4 · L3: ready drafts from the files and sources (docs/plans/business-model-
levels.md, "Items Othor AI can find from the files and sources … are offered as ready drafts").

One model call (`level_drafts` v1) reads what setup already read: the owner's business message,
the items read from their uploads (targets, terms, ways they split numbers, each with its file and
place; people's names were never read), and the names of their numbers, breakdowns and tables (no
rows, rule 3). It returns at most one draft per missing checklist item. Each draft's changes go
through the chat's own checks (`othor.home.changes.check`: allowed types and fields, the record
schema, every figure in the owner's words or files, columns that exist and aren't personal) and
its effect is computed by code. A draft is kept as the one chat's set in its own thread
("ontology:level:<item>"), so its Apply is the chat's Apply: one Business Model version, nothing
written before it (rule 6). Without a model nothing is drafted; the checklist and its sentences
still show (rule 4)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.chat import changes as cc
from othor.core.events import record_event
from othor.home import changes as hc
from othor.ontology import levels, service
from othor.setup.models import ChatMessage

PROMPT, VERSION = "level_drafts", "v1"
PREFIX = "ontology:level:"
MAX_DRAFTS = 6
FILE_ITEMS = 80


def subject(item: str) -> str:
    return PREFIX + item


def is_subject(s: str) -> bool:
    return s.startswith(PREFIX) and s.removeprefix(PREFIX) in {k for k, *_ in levels.ITEMS}


def inputs(s: Session, lv: levels.Levels, today: dt.date) -> dict[str, Any]:
    """Names and computed results only: the owner's words, what setup read from the uploads
    (no people's names), the numbers', breakdowns' and tables' names."""
    from othor.home.answers import calendar_inputs
    from othor.ontology.compiler.catalog import load_catalog
    from othor.setup import business
    from othor.setup.models import Upload, UploadRef
    from othor.web.ontology_view import line

    files = [
        {"kind": r.kind, "text": r.text[:200], "file": u.filename, "locator": r.locator}
        for r, u in s.execute(
            sa.select(UploadRef, Upload)
            .join(Upload, Upload.id == UploadRef.upload_id)
            .where(Upload.deleted_at.is_(None), UploadRef.kind.in_(("target", "term", "dimension")))
            .order_by(Upload.filename, UploadRef.locator)
        ).all()
    ][:FILE_ITEMS]
    cat = load_catalog(s)
    return {
        "missing": [{"key": i.key, "what": i.label} for i in lv.left if not i.href],
        "business": (business.message(s).text or "")[:4000],
        "files": files,
        "records": [
            {"type": r.type, "key": r.key, "line": line(r)[:160]}
            for r in service.active(s, ["owner", "goal", "known_pattern", "customer", "vocabulary"])
        ][:60],
        "metrics": [
            {"key": r.key, "name": r.body.get("name"), "unit": r.body.get("unit", "inr")}
            for r in service.active(s, ["metric"])
            if r.body.get("listed", True)
        ][:60],
        "breakdowns": sorted(r.key for r in service.active(s, ["breakdown"])),
        "tables": [
            {
                "source": src,
                "schema": schema,
                "table": name,
                "columns": [c.name for c in info.columns.values() if not c.personal][:40],
            }
            for (src, schema, name), info in sorted(cat.tables.items())
        ][:30],
        "calendar": calendar_inputs(today),
    }


def find(s: Session, gateway: Any, company: Any, *, actor: str, today: dt.date) -> int:
    """Draft what the files and sources hold for the missing items; returns how many drafts.
    Each kept as a set in its item's thread; an earlier draft for the item is replaced."""
    from othor.ai.guards import numbers_grounded
    from othor.chat import service as chat

    lv = levels.check(s)
    inp = inputs(s, lv, today)
    if not inp["missing"] or gateway is None:
        return 0
    s.commit()  # the gateway logs the call in its own session
    got = gateway.complete(company.id, PROMPT, VERSION, inp)
    out = got.output if got.ok and isinstance(got.output, dict) else None
    if out is None:
        return 0
    wanted = {m["key"] for m in inp["missing"]}
    said = " ".join([inp["business"], *(f["text"] for f in inp["files"])])
    n = 0
    seen: set[str] = set()
    for d in (out.get("drafts") or [])[:MAX_DRAFTS]:
        if not isinstance(d, dict) or d.get("item") not in wanted or d["item"] in seen:
            continue
        raws = [r for r in (d.get("changes") or [])[: cc.MAX] if isinstance(r, dict)]
        ok = []
        for raw in raws:
            c = hc.check(s, {"change": raw, "reply": ""}, inp, said, today)
            if c.change is not None and c.change["type"] != "watch":
                ok.append(c.change)
        items = [cc.item(s, company, k, c) for k, c in enumerate(ok, start=1)]
        items = [i for i in items if i["status"] == "pick"]
        if not items:
            continue
        text = " ".join(str(d.get("text") or "").split())[:160]
        why = " ".join(str(d.get("why") or "").split())[:160]
        # words with a figure the owner didn't write are replaced by code's own line (rule 1)
        if not text or not numbers_grounded(text, {**inp, "items": items}):
            text = "; ".join(i["after"] for i in items if i.get("after"))[:160]
        if not numbers_grounded(why, inp):
            why = ""
        t = chat.thread(s, subject(d["item"]))
        old = chat.last_set(s, t)
        if old is not None:
            old.applied = True
            old.proposal = {**(old.proposal or {}), "superseded": True}
        s.add(
            ChatMessage(
                thread_id=t.id,
                role="ai",
                text=text,
                proposal={"kind": "set", "items": items, "found": {"why": why}},
            )
        )
        seen.add(d["item"])
        n += 1
    record_event(
        s,
        actor=actor,
        action="ontology.level_drafts",
        object_type="company",
        object_id=company.id,
        after={"drafts": n, "guard": got.guard},
    )
    return n


def drafts(s: Session) -> dict[str, list[dict[str, Any]]]:
    """The ready drafts per item, for the level card: one line, where it was found, its Apply."""
    from othor.chat import service as chat
    from othor.setup.models import ChatThread

    out: dict[str, list[dict[str, Any]]] = {}
    for t in s.scalars(sa.select(ChatThread).where(ChatThread.subject.like(PREFIX + "%"))):
        m = chat.last_set(s, t)
        if m is None or not is_subject(t.subject):
            continue
        out.setdefault(t.subject.removeprefix(PREFIX), []).append(
            {
                "subject": t.subject,
                "text": m.text,
                "why": ((m.proposal or {}).get("found") or {}).get("why", ""),
                "n": len((m.proposal or {}).get("items") or []),
            }
        )
    return out
