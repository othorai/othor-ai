# SPDX-License-Identifier: Apache-2.0
"""`suggest_pairs` v1 (v0.12, docs/06): after seal, Othor AI ranks the suggested pairs from metric
names, units and kinds only. Its order only breaks ties in code's evidence, its reason is kept
only without a number (the numbers guard), a pair the data doesn't support is ignored, and with
no model the cards stay as code ranked them. Every call is logged with its prompt id."""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa

from othor.ai.gateway import ModelGateway, ProviderReply, TemplateProvider
from othor.ai.models import ModelCall
from othor.core.tenancy import tenant_scope
from othor.home import suggest_ai
from othor.ontology import suggest as sg
from othor.ontology.models import Question
from tests.unit.ontology.test_suggest import RUN, _planted, _points, _spec

SPEC = _spec("orders", "invoices", "cash", "returns")


class Scripted:
    name = "fake"
    configured = True

    def __init__(self, out: dict) -> None:
        self.out = out
        self.sent: list = []

    def call(self, system, messages, schema, max_tokens=0):
        self.sent.append(messages)
        return ProviderReply(json.dumps(self.out), "fake-model", 10, 10)


def _question(sf, cid) -> str:
    f = sg.pairs(SPEC, _points(_planted()), RUN)
    with sf() as s, tenant_scope(cid):
        q = Question(
            text=sg.QUESTION["pair"],
            why=sg.EXPLAIN["pair"],
            fingerprint="needs-pair-test",
            kind="needs",
            subject={"needs": "pair", "detectors": ["lead_lag"], **f.as_subject()},
            quick_replies=["We don't have this"],
            origin={"rule": "detector_needs", "needs": "pair"},
        )
        s.add(q)
        s.commit()
        return str(q.id)


def _subject(sf, cid, qid) -> dict:
    with sf() as s, tenant_scope(cid):
        return dict(s.get(Question, uuid.UUID(qid)).subject)


def test_the_model_sees_names_units_and_kinds_only(session_factory, two_companies) -> None:
    cid, _ = two_companies
    _question(session_factory, cid)
    fake = Scripted({"pairs": [{"a": "orders", "b": "invoices", "reason": "Orders get invoiced"}]})
    with session_factory() as s, tenant_scope(cid):
        assert suggest_ai.rank_pairs(s, ModelGateway(session_factory, fake), cid, SPEC) == 1
        s.commit()
    sent = json.dumps(fake.sent)
    assert "Orders" in sent and "inr" in sent
    for computed in ("agree", "rho", "weeks"):  # no computed evidence goes to the model
        assert computed not in sent
    with session_factory() as s, tenant_scope(cid):
        (call,) = s.scalars(sa.select(ModelCall).where(ModelCall.prompt_id == "suggest_pairs"))
        assert call.prompt_version == "v1"


def test_a_reason_with_a_number_is_dropped_and_an_unsupported_pair_ignored(
    session_factory, two_companies
) -> None:
    cid, _ = two_companies
    qid = _question(session_factory, cid)
    before = _subject(session_factory, cid, qid)
    fake = Scripted(
        {
            "pairs": [
                {"a": "cash", "b": "returns", "reason": "Both are money"},  # not supported
                {"a": "invoices", "b": "orders", "reason": "Invoices follow 90% of orders"},
            ]
        }
    )
    with session_factory() as s, tenant_scope(cid):
        suggest_ai.rank_pairs(s, ModelGateway(session_factory, fake), cid, SPEC)
        s.commit()
    after = _subject(session_factory, cid, qid)
    top = after["suggestions"][0]
    assert top["key"] == before["suggestions"][0]["key"]
    assert "90" not in top["sub"] and top["sub"] == before["suggestions"][0]["sub"]
    assert all(c["key"] != "pair:cash:returns" for c in after["suggestions"])


def test_a_clean_reason_is_added_to_codes_line(session_factory, two_companies) -> None:
    cid, _ = two_companies
    qid = _question(session_factory, cid)
    fake = Scripted({"pairs": [{"a": "invoices", "b": "orders", "reason": "Orders get invoiced."}]})
    with session_factory() as s, tenant_scope(cid):
        suggest_ai.rank_pairs(s, ModelGateway(session_factory, fake), cid, SPEC)
        s.commit()
    top = _subject(session_factory, cid, qid)["suggestions"][0]
    assert top["sub"].startswith("Moved the same way in ")
    assert top["sub"].endswith(" · Orders get invoiced") and top["by"] == "code+ai"


def test_with_no_model_codes_ranking_stays(session_factory, two_companies) -> None:
    cid, _ = two_companies
    qid = _question(session_factory, cid)
    before = _subject(session_factory, cid, qid)
    with session_factory() as s, tenant_scope(cid):
        gw = ModelGateway(session_factory, TemplateProvider())
        assert suggest_ai.rank_pairs(s, gw, cid, SPEC) == 0
        s.commit()
    assert _subject(session_factory, cid, qid) == before


def test_the_prompt_is_versioned_with_a_strict_schema() -> None:
    from othor.ai.gateway import load_prompt

    system, schema = load_prompt(*suggest_ai.PROMPT)
    assert "never compute or quote a number" in system
    item = schema["properties"]["pairs"]["items"]
    assert item["additionalProperties"] is False and item["required"] == ["a", "b", "reason"]
