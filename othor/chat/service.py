# SPDX-License-Identifier: Apache-2.0
"""The Othor AI chat's conversation (Chat 1.0): one thread per subject, stored as `ChatThread`
(purpose "othor") and its `ChatMessage`s. Othor AI's messages carry the pending set in
`proposal = {"items": [...], "kind": "set" | "reply" | "applied", ...}`.

- `open_set`: the subject's current set (a fresh one when the last was applied or is stale);
- `reply`: the owner's words → one model call (the thread as history, the set as names and
  statuses, never a row or a preview value: non-negotiable 3) → edits checked by `pending.edit`
  → a new message with the edited set. Never applies (non-negotiable 6).
- `choose`: the same edit from a button (the chat without a model, rule 4).
- `apply`: the owner's Apply: every ready item, one Business Model version."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.chat import pending, questions
from othor.core.events import record_event
from othor.setup.models import ChatMessage, ChatThread

PURPOSE = "othor"
PROMPT, VERSION = "othor_chat", "v1"
HISTORY = 12  # the turns a reply's model call sees
SUBJECTS = ("questions", "ask")


def known(subject: str) -> bool:
    from othor.chat import changes, proposals
    from othor.core import plugins

    return (
        subject in SUBJECTS
        or changes.is_subject(subject)
        or proposals.is_subject(subject)
        or plugins.is_ask_subject(subject)  # 0.29: a plug-in's Ask ("ask:discover:…")
    )


def prop(m: ChatMessage) -> dict[str, Any]:
    """A message's proposal (the set), never None."""
    return dict(m.proposal or {})


def thread(s: Session, subject: str) -> ChatThread:
    subject = subject if known(subject) else "ask"
    t = s.scalars(
        sa.select(ChatThread).where(ChatThread.purpose == PURPOSE, ChatThread.subject == subject)
    ).first()
    if t is None:
        t = ChatThread(purpose=PURPOSE, subject=subject, status="open")
        s.add(t)
        s.flush()
    return t


def messages(s: Session, t: ChatThread) -> list[ChatMessage]:
    return list(
        s.scalars(
            sa.select(ChatMessage)
            .where(ChatMessage.thread_id == t.id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
    )


def last_set(s: Session, t: ChatThread) -> ChatMessage | None:
    """Othor AI's latest message with a set not yet applied."""
    for m in reversed(messages(s, t)):
        if m.role == "ai" and (m.proposal or {}).get("items") is not None:
            return None if m.applied else m
    return None


def _ids(items: list[dict[str, Any]]) -> set[str]:
    return {str(i["id"]) for i in items}


def open_set(s: Session, t: ChatThread, today: dt.date) -> ChatMessage | None:
    """The questions subject's current set; a new message when there's none or the questions
    changed since (answered elsewhere, new ones raised at night)."""
    if t.subject != "questions":
        return last_set(s, t)
    fresh = questions.build(s, today)
    m = last_set(s, t)
    if m is not None and _ids(prop(m)["items"]) == _ids(fresh):
        return m
    if not fresh:
        return None
    c = pending.counts(fresh)
    m = ChatMessage(
        thread_id=t.id,
        role="ai",
        text=intro(c),
        proposal={"kind": "set", "items": fresh},
    )
    s.add(m)
    s.flush()
    return m


def intro(c: dict[str, int]) -> str:
    n, picked, need = c["total"], c["picked"], c["needs_you"]
    q = "question" if n == 1 else "questions"
    head = f"I have {n} {q} that would make Sweep and your briefs sharper. Here's what I found."
    if picked and need:
        return f"{head} I've suggested answers for {picked}, and {need} need you."
    if picked:
        return f"{head} I've suggested an answer for {'it' if n == 1 else 'each'}."
    return f"{head} {'It needs' if n == 1 else 'They need'} you."


def model_input(items: list[dict[str, Any]], text: str) -> dict[str, Any]:
    """Names and statuses only: never a preview row or a value read from a source (rule 3)."""
    return {
        "reply": text[:1000],
        "items": [
            {
                "id": i["id"],
                "text": i["text"],
                "status": i["status"],
                "answer_type": i["answer_type"],
                "chosen": i.get("choice"),
                "options": [{"key": o["key"], "label": o["label"]} for o in i.get("options") or []],
            }
            for i in items
        ],
    }


def _history(s: Session, t: ChatThread) -> tuple[dict[str, Any], ...]:
    """The last turns as the provider needs them: starting with the owner, alternating (Othor
    AI's opening message is dropped; two turns in a row by one side are joined)."""
    out: list[dict[str, Any]] = []
    for m in messages(s, t)[-HISTORY:]:
        role = "user" if m.role == "user" else "assistant"
        if not out and role == "assistant":
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + m.text
        else:
            out.append({"role": role, "content": m.text})
    if out and out[-1]["role"] == "user":  # the reply being sent now follows; never two in a row
        out.append({"role": "assistant", "content": "…"})
    return tuple(out)


def reply(
    s: Session, gateway: Any, company_id: Any, t: ChatThread, text: str, *, actor: str
) -> ChatMessage | None:
    """The owner's reply to the current set. Returns Othor AI's new message (None: no set)."""
    from othor.ai.guards import numbers_grounded

    m = last_set(s, t)
    text = text.strip()[:1000]
    if m is None or not text:
        return None
    hist = _history(s, t)
    s.add(ChatMessage(thread_id=t.id, role="user", text=text))
    s.commit()  # the gateway logs the call in its own session: no write held open here
    items = list(prop(m)["items"])
    inputs = model_input(items, text)
    out: dict[str, Any] | None = None
    guard = "template:no-model"
    if gateway is not None:
        got = gateway.complete(company_id, PROMPT, VERSION, inputs, history=hist)
        guard = got.guard
        out = got.output if got.ok and isinstance(got.output, dict) else None
    if out is None:
        say = "I couldn't read that just now. Choose for each one below, then Apply."
        new = ChatMessage(
            thread_id=t.id,
            role="ai",
            text=say,
            proposal={**prop(m), "kind": "reply", "buttons": True},
        )
    elif out.get("clarify"):
        new = ChatMessage(
            thread_id=t.id,
            role="ai",
            text=str(out["clarify"])[:300],
            proposal={**prop(m), "kind": "reply"},
        )
    else:
        edited, dropped = pending.edit(items, out.get("edits"))
        said = str(out.get("reply") or "").strip()[:400]
        if not said or not numbers_grounded(said, inputs):
            said = "Here's the list with your changes."
        new = ChatMessage(
            thread_id=t.id,
            role="ai",
            text=said,
            proposal={"kind": "reply", "items": edited, "dropped": dropped},
        )
    m.applied = True  # superseded: the new message holds the set now
    m.proposal = {**prop(m), "superseded": True}
    s.add(new)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="othor_chat.reply",
        object_type="chat_thread",
        object_id=t.id,
        after={"subject": t.subject, "guard": guard},
    )
    return new


def choose(
    s: Session, t: ChatThread, item_id: str, option: str, *, actor: str
) -> ChatMessage | None:
    """A button's choice (the chat without a model, or a quick change): the same edit a reply
    makes, checked the same way."""
    m = last_set(s, t)
    if m is None:
        return None
    action = (
        {"id": item_id, "action": "skip"}
        if option == "__skip"
        else {"id": item_id, "action": "pick", "option": option}
    )
    edited, dropped = pending.edit(list(prop(m)["items"]), [action])
    if dropped:
        return m
    m.proposal = {**prop(m), "items": edited}
    return m


def apply(
    s: Session,
    t: ChatThread,
    *,
    actor: str,
    now: dt.datetime,
    today: dt.date,
    only: str | None = None,
) -> ChatMessage | None:
    """The owner's Apply: every ready item of the current set in one Business Model version;
    skipped ones set aside for a week. Othor AI's message says what each answer does."""
    m = last_set(s, t)
    if m is None:
        return None
    every = list(prop(m)["items"])
    # the detail view's Apply (0.26.2): that one question; the rest stay as they are
    items = [i for i in every if i["id"] == only] if only else every
    rest = [i for i in every if i["id"] != only] if only else []
    out = questions.apply(s, items, actor=actor, now=now, today=today)
    skipped = questions.skip(s, items, actor=actor, today=today)
    m.applied = True
    done = ChatMessage(
        thread_id=t.id,
        role="ai",
        text=applied_text(out, skipped),
        proposal={
            "kind": "applied",
            "applied": out["applied"],
            "version": out["version"],
            "skipped": skipped,
            "not_applied": out["not_applied"],
        },
        applied=True,
    )
    s.add(done)
    s.flush()
    if rest:  # the others, as the owner left them
        s.add(
            ChatMessage(
                thread_id=t.id,
                role="ai",
                text="Here are the rest.",
                proposal={"kind": "set", "items": rest},
            )
        )
        s.flush()
    record_event(
        s,
        actor=actor,
        action="othor_chat.applied",
        object_type="chat_thread",
        object_id=t.id,
        after={
            "subject": t.subject,
            "version": out["version"],
            "answers": len(out["applied"]),
            "skipped": len(skipped),
            "drafted_by": "othor_ai",  # 0.35: the owner applied Othor AI's draft
        },
    )
    return done


def applied_text(out: dict[str, Any], skipped: list[str]) -> str:
    n = len(out["applied"])
    head = f"Applied {n} answer{'s' if n != 1 else ''}." if n else "Nothing was applied."
    tail = f" I'll ask about {len(skipped)} again next week." if skipped else ""
    missed = len(out.get("not_applied") or [])
    if missed:  # its answer can't be written now (what it refers to has changed): said, not hidden
        what = "it refers" if missed == 1 else "they refer"
        tail += f" {missed} couldn't be applied: what {what} to has changed, so I'll ask again."
    return head + tail + " You can change any of them on the Business Model page."


def dumps(items: list[dict[str, Any]]) -> str:  # for tests and logs: the set, stably
    return json.dumps(items, sort_keys=True, ensure_ascii=False)
