# SPDX-License-Identifier: Apache-2.0
"""`size_numbers` v1 (#76, docs/06): after seal, Othor AI words only the reason of each number
suggested for the company's size. It sees names, the band in words and the goals' names and
states, never a figure; a reason with a number in it (numerals or words) is dropped by the numbers
guard; with no model the catalogue's template reasons stay. Every call is logged with its id."""

from __future__ import annotations

import json

import sqlalchemy as sa

from othor.ai.gateway import ModelGateway, ProviderReply, TemplateProvider
from othor.ai.guards import numerals
from othor.ai.models import ModelCall
from othor.core.tenancy import tenant_scope
from othor.home import size_ai
from othor.ontology import size_numbers
from tests.unit.ontology.test_size_numbers import _propose, _size, _spec


class Scripted:
    name = "fake"
    configured = True

    def __init__(self, out: dict) -> None:
        self.out = out
        self.sent: list = []

    def call(self, system, messages, schema, max_tokens=0):
        self.sent.append(messages)
        return ProviderReply(json.dumps(self.out), "fake-model", 10, 10)


REASONS = {
    "reasons": [
        {"key": "top10_share", "reason": "A few big buyers decide your year, so watch them."},
        {"key": "gross_margin_pct", "reason": "Margin tells you 36% of the story early."},
        {"key": "revenue_per_active_customer", "reason": "Two in three firms track it."},
        {"key": "made_up", "reason": "Not one of yours."},
    ]
}


def _lines(sf, cid) -> dict[str, dict]:
    with sf() as s, tenant_scope(cid):
        return {p.evidence["key"]: dict(p.evidence) for p in size_numbers.open_rows(s)}


def test_the_model_words_the_reason_and_a_number_is_dropped(session_factory, two_companies):
    cid, _ = two_companies
    spec = _spec()
    _propose(None, session_factory, cid, spec)
    fake = Scripted(REASONS)
    with session_factory() as s, tenant_scope(cid):
        n = size_ai.word_reasons(s, ModelGateway(session_factory, fake), cid, spec)
        s.commit()
    assert n == 1
    got = _lines(session_factory, cid)
    top = got["top10_share"]
    assert top["reason_by"] == "othor_ai" and top["prompt"] == "size_numbers@v1"
    assert top["reason"] == "A few big buyers decide your year, so watch them"
    # the figure stays code's, in the line beside Othor AI's words
    assert top["line"].startswith("Your 10 largest customers carried 90% of net revenue")
    assert top["line"].endswith("A few big buyers decide your year, so watch them")
    # "36%" and "Two in three": numbers from the model, dropped; the catalogue's words stay
    for key in ("gross_margin_pct", "revenue_per_active_customer"):
        assert got[key]["reason_by"] == "template"
        assert numerals(got[key]["reason"]) == set()
    # what the model saw: names and words, never a computed figure
    sent = json.dumps(fake.sent, ensure_ascii=False)
    sz = _size(spec)
    for f in sz["figures"].values():
        assert f["value"] not in sent
    assert sz["revenue"] not in sent and sz["crore"] not in sent
    with session_factory() as s, tenant_scope(cid):
        calls = s.scalars(sa.select(ModelCall)).all()
        assert [(c.prompt_id, c.prompt_version) for c in calls] == [("size_numbers", "v1")]
        # asked once: a second night makes no second call for the same suggestions
        size_ai.word_reasons(s, ModelGateway(session_factory, fake), cid, spec)
    assert len(fake.sent) == 1


def test_no_model_keeps_the_template_reasons(session_factory, two_companies):
    cid, _ = two_companies
    spec = _spec()
    _propose(None, session_factory, cid, spec)
    before = _lines(session_factory, cid)
    with session_factory() as s, tenant_scope(cid):
        gw = ModelGateway(session_factory, TemplateProvider())
        assert size_ai.word_reasons(s, gw, cid, spec) == 0
        s.commit()
    after = _lines(session_factory, cid)
    assert after == before
    assert (
        after["top10_share"]["reason"]
        == "At your size, one of them going quiet moves the whole year"
    )
    assert all(e["reason_by"] == "template" and not e.get("worded") for e in after.values())
