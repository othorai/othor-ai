# SPDX-License-Identifier: Apache-2.0
"""The Othor AI chat panel's view (Chat 1.0, design/questions-chat/): the thread's messages,
Othor AI's set as numbered items (status badge, the pick with its source tag, other choices, what
it does), Apply N and Suggest edits, the reply box once asked for. Opened in place on any page by
`?panel=othor&subject=…` (as Contact us is)."""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import urlencode

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.chat import pending
from othor.chat import service as chat

BADGE = {
    "pick": ("My pick", "pick"),
    "needs_you": ("Needs you", "needs"),
    "changed": ("Changed", "changed"),
    "skipped": ("Skipped", "skipped"),
}
TITLE = {"questions": "Questions for you", "ask": "Ask Othor AI"}


def _source_names(s: Session) -> dict[str, str]:
    """A source's plain name (its display name once it has one, M26.3), by its name."""
    from othor.connectors.models import Source

    out: dict[str, str] = {}
    for src in s.scalars(sa.select(Source)):
        out[src.name] = getattr(src, "display_name", None) or src.name
    return out


def _tag(tag: dict[str, str] | None, names: dict[str, str]) -> dict[str, str] | None:
    if not tag:
        return None
    plain = names.get(tag["source"], tag["source"])
    return {
        "plain": plain,
        "source": tag["source"],
        "table": tag["table"],
        "same": plain == tag["source"],
    }


def _card(c: dict[str, Any] | None) -> dict[str, Any] | None:
    if not c:
        return None
    pv = c.get("provenance") or {}
    where = []
    if pv.get("run"):
        where.append(f"Run #{int(pv['run']):04d} of {pv.get('run_date', '')}")
    if pv.get("version"):
        where.append(f"Business Model v{pv['version']}")
    if pv.get("spec"):
        where.append(f"spec {pv['spec']}")
    return {
        "title": c.get("title") or "",
        "facts": [tuple(f) for f in c.get("facts") or []],
        "lines": list(c.get("lines") or []),
        "where": " · ".join(where),
        "tool": c.get("tool") or "",
    }


def _preview_url(item_id: str, o: dict[str, Any]) -> str:
    """Where a choice's real rows are read from, live (M26.3); "" when there's nothing to read."""
    if not o.get("preview"):
        return ""
    return "/othor/preview?" + urlencode({"item": item_id, "option": o["key"]})


def _items(
    raw: list[dict[str, Any]], names: dict[str, str], subject: str = ""
) -> list[dict[str, Any]]:
    out = []
    for n, i in enumerate(raw, start=1):
        opts = i.get("options") or []
        chosen = next((o for o in opts if o.get("key") == i.get("choice")), None)
        label, cls = BADGE.get(i.get("status", ""), ("", ""))
        if i.get("kind") == "change" and i.get("status") == "pick":
            label = "Draft"  # the owner's own words, drafted: not Othor AI's pick
        out.append(
            {
                "n": n,
                "id": i["id"],
                "text": i["text"],
                "badge": label,
                "badge_class": cls,
                "pick": (
                    {
                        "label": chosen["label"],
                        "tag": _tag(chosen.get("tag"), names),
                        "preview": _preview_url(i["id"], chosen),
                    }
                    if chosen
                    else None
                ),
                "answer": i.get("answer"),
                "others": [
                    {
                        "key": o["key"],
                        "label": o["label"],
                        "note": o.get("note") or "",
                        "tag": _tag(o.get("tag"), names),
                        "preview": _preview_url(i["id"], o),
                    }
                    for o in opts
                    if o.get("key") != i.get("choice")
                ],
                "free_text": i.get("answer_type") == "free_text",
                "effect": i.get("effect") or "",
                "why_not": i.get("why_not") or "",
                "status": i.get("status"),
                # a Business Model change (M26.4): the line before and after, its effect lines
                "change": i.get("kind") == "change",
                "before": i.get("before") or "",
                "after": i.get("after") or "",
                "lines": list(i.get("lines") or []),
                "values_url": (
                    "/othor/preview?" + urlencode({"subject": subject, "item": i["id"]})
                    if i.get("kind") == "change" and i.get("high_impact") and subject
                    else ""
                ),
            }
        )
    return out


def view(
    s: Session, request: Any, subject: str, *, editing: bool, today: dt.date
) -> dict[str, Any]:
    from othor.chat import changes, proposals

    t = chat.thread(s, subject)
    is_change = changes.is_subject(t.subject)
    is_proposal = proposals.is_subject(t.subject)
    if is_proposal:
        proposals.open_set(s, t)
        s.commit()
    if t.subject == "questions":
        chat.open_set(s, t, today)
        s.commit()
    names = _source_names(s)
    msgs = []
    current = chat.last_set(s, t)
    for m in chat.messages(s, t):
        p = m.proposal or {}
        msgs.append(
            {
                "role": m.role,
                "text": m.text,
                "rows": _items(p.get("items") or [], names, t.subject) if m is current else [],
                "applied": [
                    {**a, "effect": a.get("effect") or ""} for a in (p.get("applied") or [])
                ]
                if p.get("kind") == "applied"
                else [],
                "buttons": bool(p.get("buttons")) and m is current,
                # Ask (M26.6): the tool's card, computed by code, with where it came from
                "card": _card(p.get("card")) if p.get("kind") == "answer" else None,
                "to_change": bool(p.get("change")),
                "current": m is current,
            }
        )
    here = request.url.path
    rest = [
        (k, v)
        for k, v in request.query_params.multi_items()
        if k not in ("panel", "subject", "edit")
    ]
    base = here + ("?" + urlencode(rest) if rest else "")
    sep = "&" if "?" in base else "?"
    open_url = f"{base}{sep}panel=othor&subject={t.subject}"
    counts = pending.counts(current.proposal["items"]) if current is not None else None
    applied_once = any(m.get("applied") for m in msgs)
    return {
        "subject": t.subject,
        "title": (
            changes.title(s, t.subject)
            if is_change
            else "About this proposal"
            if is_proposal
            else TITLE.get(t.subject, "Othor AI")
        ),
        "is_proposal": is_proposal,
        "opening": (
            changes.opening(t.subject)
            if is_change and not msgs
            else "Ask me about your numbers, what Sweep found, which checks ran, your Business "
            "Model's changes or the questions waiting for you. Every figure I give is computed."
            if t.subject == "ask" and not msgs
            else ""
        ),
        "is_change": is_change,
        "messages": msgs,
        "counts": counts,
        "has_set": current is not None,
        "editing": editing or applied_once or current is None or is_proposal,
        "applied_once": applied_once,
        "close": base,
        "here": open_url,
        "edit_url": open_url + "&edit=1",
        "empty": t.subject == "questions" and current is None and not msgs,
    }
