# SPDX-License-Identifier: Apache-2.0
"""Home's proposals in the one Othor AI chat (0.26, M26.4): "Chat about this" opens the chat on
`proposal:<id>`. Othor AI's message holds the proposal as the set's one item (what it is, where
it comes from, how sure, all computed at night); the owner's questions are answered from its
evidence only (`proposal_chat` v1, a reply with a number not in the evidence is not shown);
Apply accepts it (the same `proposals.decide` as Home's Accept, then the spec is compiled), and
skipping it then Apply declines it. Nothing changes until Apply (non-negotiable 6)."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.chat import pending
from othor.chat.service import prop
from othor.core.events import record_event
from othor.engine.models import Proposal
from othor.setup.models import ChatMessage, ChatThread

PREFIX = "proposal:"
NO_MODEL = "I can't answer just now. Apply it or skip it below; your choice stays the same."
ONLY_EVIDENCE = "I can only speak to the evidence shown above."


def is_subject(subject: str) -> bool:
    return subject.startswith(PREFIX)


def proposal(s: Session, subject: str) -> Proposal | None:
    try:
        pid = uuid.UUID(subject.removeprefix(PREFIX))
    except ValueError:
        return None
    return s.scalars(sa.select(Proposal).where(Proposal.id == pid)).first()


def subject_ok(s: Session, subject: str) -> bool:
    return proposal(s, subject) is not None


def item(p: Proposal) -> dict[str, Any]:
    from othor.web import home_view as hv

    row = hv.proposal_row(p)
    return {
        "id": f"p:{p.id}",
        "kind": "proposal",
        "text": row["text"],
        "answer_type": "choice",
        "options": [
            {"key": "accept", "label": row["line"] or "Accept it", "note": "", "tag": None}
        ],
        "choice": "accept",
        "status": "pick",
        "effect": f"{row['kind']} · from {row['from']} · {row['confidence']} confidence · "
        "applies from the next run",
    }


def open_set(s: Session, t: ChatThread) -> ChatMessage | None:
    """The proposal as the set, while it's open; none once accepted or declined."""
    from othor.chat import service as chat

    p = proposal(s, t.subject)
    m = chat.last_set(s, t)
    if p is None or p.status != "open":
        if m is not None:
            m.applied = True
        return None
    if m is not None:
        return m
    m = ChatMessage(
        thread_id=t.id,
        role="ai",
        text="Here's what I'm proposing and why. Ask me anything about it, or apply it.",
        proposal={"kind": "set", "items": [item(p)]},
    )
    s.add(m)
    s.flush()
    return m


def reply(
    s: Session, gateway: Any, company_id: Any, t: ChatThread, text: str, *, actor: str
) -> ChatMessage | None:
    """The owner's question about the proposal, answered from its evidence; the set stays."""
    from othor.ai.guards import numbers_grounded
    from othor.chat import service as chat

    text = text.strip()[:500]
    p = proposal(s, t.subject)
    if not text or p is None:
        return None
    m = chat.last_set(s, t)
    convo = [{"role": x.role, "text": x.text} for x in chat.messages(s, t)][-8:]
    inputs = {
        "proposal": {"kind": p.kind, "text": p.text, "evidence": p.evidence},
        "conversation": convo,
        "question": text,
    }
    s.add(ChatMessage(thread_id=t.id, role="user", text=text))
    s.commit()  # the gateway logs the call in its own session
    said = NO_MODEL
    guard = "template:no-model"
    if gateway is not None:
        got = gateway.complete(company_id, "proposal_chat", "v1", inputs)
        guard = got.guard
        out = got.output if got.ok and isinstance(got.output, dict) else None
        if out is not None:
            said = str(out.get("reply") or "")[:2000]
            if not said or not numbers_grounded(said, inputs):
                said = ONLY_EVIDENCE
    new = ChatMessage(
        thread_id=t.id,
        role="ai",
        text=said,
        proposal={**prop(m), "kind": "reply"} if m is not None else {"kind": "reply"},
    )
    if m is not None:
        m.applied = True  # the set moves to the new message
        m.proposal = {**prop(m), "superseded": True}
    s.add(new)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="proposal.chat",
        object_type="proposal",
        object_id=p.id,
        after={"guard": guard},
    )
    return new


def apply(
    s: Session, company: Any, t: ChatThread, *, actor: str, now: Any, decline: bool = False
) -> ChatMessage | None:
    """Accept (ready) or decline (Decline, or skipped), as Home's buttons do; then compiled."""
    from othor.chat import service as chat
    from othor.engine import proposals
    from othor.ontology.compiler import compile_version

    m = chat.last_set(s, t)
    p = proposal(s, t.subject)
    if m is None or p is None or p.status != "open":
        return None
    it = prop(m)["items"][0]
    accept = pending.ready(it) and not decline
    if not accept and not decline and it.get("status") != "skipped":
        return None
    proposals.decide(s, p, accept=accept, actor=actor, now=now)
    if accept:
        compile_version(s, company, actor=actor)
    m.applied = True
    done = ChatMessage(
        thread_id=t.id,
        role="ai",
        text=(
            "Applied. It's in your Business Model now; you can undo it on Home."
            if accept
            else "Declined. Nothing changed."
        ),
        proposal={"kind": "applied", "applied": [], "accepted": accept},
        applied=True,
    )
    s.add(done)
    s.flush()
    return done
