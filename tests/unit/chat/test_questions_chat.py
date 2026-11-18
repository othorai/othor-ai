# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F811  (the ontology tests' fixtures, imported by name)
"""Questions for you as one message (Chat 1.0, the owner's handoff): every waiting question is an
item; Othor AI picks only a strong answer that can't rewrite past findings; fixed-reply questions
need the owner; Apply writes every ready answer in one Business Model version, an event each;
skipped questions come back in a week."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

import sqlalchemy as sa

from othor.chat import pending, questions
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.ontology import service
from othor.ontology.models import Question
from tests.unit.ontology.conftest import sample, world  # noqa: F401  (fixtures)

TODAY = dt.date(2026, 11, 2)
NOW = dt.datetime(2026, 11, 2, 4, 0, tzinfo=dt.UTC)


def _q(
    s: Any,
    kind: str,
    subject: dict[str, Any],
    replies: tuple[str, ...] = (),
    rank: str = "2|",
    text: str = "A question",
) -> Question:
    q = Question(
        text=text,
        why="",
        origin={},
        quick_replies=list(replies),
        fingerprint=uuid.uuid4().hex,
        kind=kind,
        subject=subject,
        rank_key=rank,
        unblocks={"detectors": ["pair_break", "lead_lag"]} if kind == "needs" else None,
    )
    s.add(q)
    s.flush()
    return q


def _card(key: str, change: dict[str, Any], strong: bool = True) -> dict[str, Any]:
    return {
        "key": key,
        "label": key.title(),
        "sub": "Moved the same way in 40 of 52 weeks",
        "change": change,
        "strong": strong,
    }


PAIR = {
    "type": "rule",
    "key": "pair_net_revenue_invoiced_value",
    "body": {
        "kind": "pair",
        "text": "Net revenue moves with invoiced value",
        "over": ["net_revenue", "invoiced_value"],
        "relation": "together",
    },
}


def test_picks_needs_you_and_one_version_for_every_answer(world) -> None:  # type: ignore[no-untyped-def]
    with world.sf() as s, tenant_scope(world.cid):
        a = _q(
            s,
            "needs",
            {"suggestions": [_card("pair:a", PAIR)], "decide": {"pick": "pair:a"}},
            rank="1|",
            text="Which numbers move together?",
        )
        metric = {"type": "metric", "key": "net_revenue", "body": {"name": "Net revenue"}}
        b = _q(
            s,
            "needs",
            {"suggestions": [_card("m:1", metric)], "decide": {"pick": "m:1"}},
            rank="2|",
            text="Which numbers are your order values?",
        )
        c = _q(
            s,
            "pattern",
            {"pattern": "surat_shutdown"},
            ("Yes, every year", "No, the dates change"),
            rank="3|",
            text="Is the Surat shutdown every year?",
        )
        items = questions.build(s, TODAY)
        by = {i["id"]: i for i in items}
        ia, ib, ic = by[f"q:{a.id}"], by[f"q:{b.id}"], by[f"q:{c.id}"]
        assert ia["status"] == "pick" and ia["choice"] == "pair:a"
        assert "2 more checks" in ia["effect"]
        # a metric answer could rewrite past findings: the owner's, never Othor AI's pick
        assert ib["status"] == "needs_you" and ib["high_impact"] and ib["choice"] is None
        assert ic["status"] == "needs_you" and [o["key"] for o in ic["options"]] == [
            "Yes, every year"
        ]
        assert pending.counts(items)["apply"] == 1

        # the owner's reply picks the pattern's answer; the metric one is skipped
        items, dropped = pending.edit(
            items,
            [
                {"id": ic["id"], "action": "pick", "option": "Yes, every year"},
                {"id": ib["id"], "action": "skip"},
            ],
        )
        assert dropped == 0
        n = service.current_number(s)
        out = questions.apply(s, items, actor="o", now=NOW, today=TODAY)
        assert service.current_number(s) == n + 1  # one version for both answers
        assert {d["id"] for d in out["applied"]} == {ia["id"], ic["id"]} and not out["not_applied"]
        assert a.status == "answered" and c.status == "answered" and b.status == "open"
        assert a.answer_version == c.answer_version == out["version"]
        evs = [e for e in s.scalars(sa.select(Event).where(Event.action == "question.answered"))]
        assert len(evs) == 2 and {e.after["picked_by"] for e in evs} == {"othor_ai", "owner"}

        questions.skip(s, items, actor="o", today=TODAY)
        assert b.status == "skipped" and b.skipped_until == TODAY + dt.timedelta(days=7)


def test_nothing_ready_applies_nothing(world) -> None:  # type: ignore[no-untyped-def]
    with world.sf() as s, tenant_scope(world.cid):
        _q(s, "pattern", {"pattern": "x"}, ("Yes, every year",))
        n = service.current_number(s)
        out = questions.apply(s, questions.build(s, TODAY), actor="o", now=NOW, today=TODAY)
        assert out == {"applied": [], "version": None, "not_applied": []}
        assert service.current_number(s) == n


class _Got:
    def __init__(self, output: Any, guard: str = "passed") -> None:
        self.output, self.guard = output, guard

    @property
    def ok(self) -> bool:
        return self.guard == "passed" and self.output is not None


class _Gateway:
    def __init__(self, output: Any) -> None:
        self.output, self.calls = output, []  # type: ignore[var-annotated]

    def complete(
        self, company_id: Any, prompt: str, version: str, inputs: Any, history: Any = ()
    ) -> _Got:
        self.calls.append((prompt, inputs, history))
        return _Got(self.output)


def test_a_reply_edits_the_set_and_never_applies(world) -> None:  # type: ignore[no-untyped-def]
    from othor.chat import service as chat

    with world.sf() as s, tenant_scope(world.cid):
        a = _q(
            s,
            "needs",
            {"suggestions": [_card("pair:a", PAIR)], "decide": {"pick": "pair:a"}},
            rank="1|",
            text="Which numbers move together?",
        )
        c = _q(s, "pattern", {"pattern": "surat_shutdown"}, ("Yes, every year",), rank="3|")
        t = chat.thread(s, "questions")
        m = chat.open_set(s, t, TODAY)
        assert m is not None and "I've picked answers for 1, and 1 need you" in m.text
        assert chat.open_set(s, t, TODAY) is m  # the same set until something changes
        n = service.current_number(s)
        gw = _Gateway(
            {
                "edits": [
                    {
                        "id": f"q:{c.id}",
                        "action": "pick",
                        "option": "Yes, every year",
                        "text": None,
                    },
                    {"id": "q:nope", "action": "skip", "option": None, "text": None},
                ],
                "reply": "Marked the Surat shutdown as every year.",
                "clarify": None,
            }
        )
        new = chat.reply(s, gw, world.cid, t, "Surat is every year", actor="o")
        assert new is not None and new.proposal["dropped"] == 1
        assert service.current_number(s) == n and c.status == "open"  # a reply applies nothing
        prompt, inputs, hist = gw.calls[0]
        assert prompt == "othor_chat" and "rows" not in json.dumps(inputs)
        assert not hist or hist[0]["role"] == "user"  # the provider's order
        # an unclear reply: one short question back, the set unchanged
        gw2 = _Gateway({"edits": [], "reply": "", "clarify": "Which one do you mean?"})
        q2 = chat.reply(s, gw2, world.cid, t, "change the second one", actor="o")
        assert q2 is not None and q2.text == "Which one do you mean?"
        # without a model: the set with buttons
        q3 = chat.reply(s, None, world.cid, t, "anything", actor="o")
        assert q3 is not None and q3.proposal.get("buttons")
        done = chat.apply(s, t, actor="o", now=NOW, today=TODAY)
        assert done is not None and service.current_number(s) == n + 1
        assert a.status == "answered" and c.status == "answered"
        assert "Applied 2 answers" in done.text and chat.last_set(s, t) is None
