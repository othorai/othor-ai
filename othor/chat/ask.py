# SPDX-License-Identifier: Apache-2.0
"""Ask v0.1 in the one Othor AI chat (0.26, M26.6): "Ask Othor AI" in every top bar.

A question → `ask_route` v1 picks one read-only tool and its arguments from the registry's names,
descriptions and schemas (never a row) → the tool runs through the one gate (`othor.tools.call`:
role, schema, audit) → `ask_answer` v2 (0.28.4: short formatted paragraphs) words the answer
from the card's computed facts; a sentence with a figure the card doesn't hold is not shown (the
card shows alone), and one whose direction or count contradicts the card is dropped (#120,
`guards.contradiction`). The card is stored with the message, with where it came from; the answer's
numbers are always the card's (rule 1).

A change request ("move the Diwali goal") is drafted at once in the Business Model chat, to
review and apply there. Without a model: the question's words are matched to a tool, and the
card shows without words (rule 4)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from othor import tools
from othor.ai.slots import Slots, keep_clean
from othor.core.events import record_event
from othor.setup.models import ChatMessage, ChatThread

log = logging.getLogger(__name__)

# v3 (0.37, #122): the card's figures go as labelled slots, the model writes slots and code fills
# them; v2 stays for the provenance of answers worded before
ROUTE, ANSWER = ("ask_route", "v1"), ("ask_answer", "v3")
NONE = (
    "I can answer about your numbers, what Sweep found, which checks ran, your Business Model's "
    "changes and the questions waiting for you. Try one of those."
)
CHANGE = "I couldn't draft that as a change. Open the Business Model chat and say it another way."


def _route(
    gateway: Any, company_id: Any, text: str, hist: tuple[dict[str, Any], ...]
) -> tuple[str | None, dict[str, Any], bool, str, str]:
    """(tool, args, change?, reply, guard)."""
    if gateway is None:
        return tools.match(text), {}, False, "", "template:no-model"
    got = gateway.complete(
        company_id, *ROUTE, {"question": text, "tools": tools.listing()}, history=hist
    )
    out = got.output if got.ok and isinstance(got.output, dict) else None
    if out is None:  # the model is down: code's match
        return tools.match(text), {}, False, "", got.guard
    try:
        args = json.loads(str(out.get("args_json") or "{}"))
    except ValueError:
        args = {}
    raw = out.get("tool")
    tool = raw.strip() if isinstance(raw, str) else None
    if not tool or tool.lower() in ("null", "none"):  # #119: a model's "null" is no tool
        tool = None
    return (
        tool,
        args if isinstance(args, dict) else {},
        bool(out.get("change")),
        str(out.get("reply") or "")[:300],
        got.guard,
    )


def slotted(text: str, card: tools.Card) -> tuple[dict[str, Any], Slots]:
    """The answer's input (#122): each fact's value is its own slot, labelled ({n1} =
    "Suppressed: 2"), so one figure under two labels is two slots; numbers in the title and
    lines are slots too. The model never sees a bare figure it could put under the wrong label."""
    slots = Slots()
    facts = [
        {"slot": slots.new(str(v), f"{label}: {v}"), "label": label} for label, v in card.facts
    ]
    shown = {
        "title": slots.text(card.title),
        "facts": facts,
        "lines": [slots.text(line) for line in card.lines],
    }
    return {"question": text, "card": shown, "slots": dict(slots.labels)}, slots


def _words(gateway: Any, company_id: Any, text: str, card: tools.Card) -> str:
    """The model's words over the card, its slots filled by code. A sentence with a figure
    outside a slot is dropped; the numbers guard checks what's left against the card (second
    line)."""
    if gateway is None or card.empty:
        return ""
    inputs, slots = slotted(text, card)
    got = gateway.complete(company_id, *ANSWER, inputs)
    out = got.output if got.ok and isinstance(got.output, dict) else None
    said = str((out or {}).get("answer") or "").strip()[:1200]
    if not said:
        return ""
    allowed = json.dumps([text, inputs["card"]], ensure_ascii=False)
    kept, _ = keep_clean(
        said, slots, allowed=allowed, grounded_in={"question": text, "card": card.as_json()}
    )
    if not kept:
        return ""
    return plain_dates(_meaning(gateway, company_id, inputs, kept, card.as_json()))


def _meaning(
    gateway: Any, company_id: Any, inputs: dict[str, Any], said: str, card: dict[str, Any]
) -> str:
    """#120: a sentence whose direction or count contradicts the card ("rose" where the card's
    change is down, "Three checks ran" when five did) is dropped; the card stays. The call's
    log row says why. `card` is the computed card (`Card.as_json()`, its figures filled);
    `inputs` is what the model was sent (slotted), so the mark finds the call's row."""
    from othor.ai.guards import card_result, drop_contradictions

    kept, why = drop_contradictions(said, card_result(card))
    if why:
        guard = "dropped:" + ",".join(sorted(set(why)))
        mark_call(gateway, company_id, ANSWER, inputs, guard)
        log.info("model sentence dropped", extra={"prompt": ANSWER[0], "guard": guard})
    return kept


def mark_call(
    gateway: Any, company_id: Any, prompt: tuple[str, str], inputs: Any, guard: str
) -> None:
    """The call's log row (docs/06) gets the guard that changed its output; a call so marked is
    not reused from the cache."""
    import sqlalchemy as sa

    from othor.ai.gateway import input_hash
    from othor.ai.models import ModelCall
    from othor.core.tenancy import tenant_scope

    sf = getattr(gateway, "sf", None)
    if sf is None:
        return
    h = input_hash(prompt[0], prompt[1], inputs, [[], []])
    with sf() as s, tenant_scope(company_id):
        row = s.scalars(
            sa.select(ModelCall)
            .where(ModelCall.prompt_id == prompt[0], ModelCall.input_hash == h)
            .order_by(ModelCall.created_at.desc())
            .limit(1)
        ).first()
        if row is not None:
            row.guard_result = guard
            s.commit()


ISO_DAY = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def plain_dates(text: str) -> str:
    """A date the card carries as 2026-11-18 reads '18 Nov 2026' in the answer (#94: "Net
    revenue for 2026-10-01")."""
    import datetime as dt

    from othor.core import fmt

    def one(m: re.Match[str]) -> str:
        try:
            d = dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return m[0]
        return fmt.day_month_year(d)

    return ISO_DAY.sub(one, text)


def answer(
    s: Session,
    gateway: Any,
    tctx: tools.Ctx,
    t: ChatThread,
    text: str,
    *,
    actor: str,
    about: str = "",
) -> ChatMessage | None:
    from othor.chat import service as chat

    text = text.strip()[:500]
    if not text:
        return None
    hist = chat._history(s, t)
    s.add(ChatMessage(thread_id=t.id, role="user", text=text))
    s.commit()  # the gateway logs the call in its own session
    # 0.29: asked from a page of its own (a Discover group): the model also sees what it is about,
    # names only; the message stored is the owner's own words
    asked = f"{text}\n\n(Asked on {about})" if about else text
    name, args, change, reply, guard = _route(gateway, tctx.company.id, asked, hist)
    card: tools.Card | None = None
    drafted = None
    if change:
        # context-aware (owner, 18 Nov 2026): a change asked for in Ask is drafted straight
        # away in the Business Model chat, to review and apply there
        from othor.chat import changes
        from othor.chat import service as chat_svc

        othread = chat_svc.thread(s, "ontology")
        drafted = changes.draft(
            s, gateway, tctx.company, othread, text, actor=actor, today=tctx.today
        )
        has_set = drafted is not None and bool((drafted.proposal or {}).get("items"))
        said = (
            "I've drafted that as a change to your Business Model. Review it and apply."
            if has_set
            else CHANGE
        )
    elif name is None:
        said = reply or NONE
    else:
        try:
            card = tools.call(tctx, name, args, actor=actor, via="chat")
        except tools.Refused as e:
            said = f"I couldn't look that up: {e}."
        else:
            s.commit()  # the audit event first; the wording call logs in its own session
            tool = tools.REGISTRY.get(name)
            if tool is not None and not tool.worded:  # 2.1: code's one line, then the card
                said = card.empty or (card.lines.pop(0) if card.lines else "Here's what I found.")
            else:
                said = (
                    card.empty
                    or _words(gateway, tctx.company.id, asked, card)
                    or "Here's what I found."
                )
    new = ChatMessage(
        thread_id=t.id,
        role="ai",
        text=said,
        proposal={
            "kind": "answer",
            **({"card": card.as_json()} if card is not None else {}),
            **({"change": True} if change else {}),
        },
    )
    s.add(new)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="othor_chat.asked",
        object_type="chat_thread",
        object_id=t.id,
        after={"tool": name, "guard": guard, "change": change},
    )
    return new
