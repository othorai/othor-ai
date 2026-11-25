# SPDX-License-Identifier: Apache-2.0
"""#120 in Ask: an answer sentence whose direction or count contradicts the card is dropped (the
card stays), and the call's log row says why, so it is never served again from the cache. From
0.37 (ask_answer v3, #122) the model writes the card's figures as slots ({n1} = "Clean: 3") and
code fills them; the check reads the filled sentence."""

from __future__ import annotations

import json

import sqlalchemy as sa

from othor import tools
from othor.ai.gateway import ModelGateway, ProviderReply
from othor.ai.models import ModelCall
from othor.chat import ask
from othor.core.tenancy import tenant_scope

COVERAGE = tools.Card(
    "sweep_coverage",
    "Which checks ran in run #0041",
    facts=[("Clean", "3"), ("Could not run", "3"), ("Suppressed", "2")],
)


class Scripted:
    name = "fake"
    configured = True

    def __init__(self, answer: str) -> None:
        self.answer = answer

    def call(self, system, messages, schema, max_tokens=0):
        return ProviderReply(json.dumps({"answer": self.answer}), "fake-model", 10, 10)


def _guard(session_factory, cid) -> str:
    with session_factory() as s, tenant_scope(cid):
        (row,) = s.scalars(sa.select(ModelCall).where(ModelCall.prompt_id == "ask_answer"))
        return row.guard_result


def test_the_wrong_count_is_dropped_and_logged(session_factory, two_companies):
    cid, _ = two_companies
    said = "{n1} checks ran last night. {n1} came back clean and {n3} were held back."
    gw = ModelGateway(session_factory, Scripted(said))
    got = ask._words(gw, cid, "Which checks ran?", COVERAGE)
    assert got == "3 came back clean and 2 were held back."
    assert _guard(session_factory, cid) == "dropped:ran"


def test_a_right_answer_is_kept_whole(session_factory, two_companies):
    cid, _ = two_companies
    said = "{n1} checks ran clean and {n3} were held back.\n\n{n2} couldn't run: they need data."
    gw = ModelGateway(session_factory, Scripted(said))
    assert ask._words(gw, cid, "Which checks ran?", COVERAGE) == (
        "3 checks ran clean and 2 were held back.\n\n3 couldn't run: they need data."
    )
    assert _guard(session_factory, cid) == "passed"


def test_nothing_left_means_the_card_alone(session_factory, two_companies):
    cid, _ = two_companies
    # 2 is on the card, but not how many ran
    gw = ModelGateway(session_factory, Scripted("{n3} checks ran."))
    assert ask._words(gw, cid, "Which checks ran?", COVERAGE) == ""
