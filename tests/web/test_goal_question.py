# SPDX-License-Identifier: Apache-2.0
"""0.28 (M28.2): while the company has no goal, the night asks "What are you aiming for this
year?" with one goal suggested by code (the briefs' lead number, its target computed from the
company's own numbers; no model needed). Apply in the questions chat writes it; the question
closes once a goal exists and opens again when every goal is removed."""

from __future__ import annotations

import datetime as dt
import html

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.engine.store import read_json
from othor.ontology import goal_suggest, service
from othor.ontology.models import Question
from othor.ontology.questions import raise_questions, refresh_goal_question
from othor.web.ontology_view import run_data
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_in_place import _post
from tests.web.test_sweep_web import night  # noqa: F401

TODAY = dt.date(2026, 11, 1)


def _candidate(s, settings):  # type: ignore[no-untyped-def]
    rd = run_data(s, settings)
    spec = {**rd.spec, "forecast": {**(rd.spec.get("forecast") or {}), "goals": []}}
    fc = read_json(rd.files.outputs / "forecasts.json") or {}
    return goal_suggest.question_candidate(spec, rd.points, fc.get("rows") or [], 4, TODAY)


def _goal_q(s):  # type: ignore[no-untyped-def]
    return s.scalars(sa.select(Question).where(Question.kind == goal_suggest.KIND)).first()


def test_with_goals_nothing_is_asked(night):  # noqa: F811
    _env, _h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        rd = run_data(s, svc.settings)
        assert (rd.spec.get("forecast") or {}).get("goals")
        assert goal_suggest.question_candidate(rd.spec, rd.points, [], 4, TODAY) is None


def test_no_goal_asks_with_a_computed_goal_and_apply_writes_it(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        gone = [service.retire("goal", r.key) for r in service.active(s, ["goal"])]
        service.apply(s, gone, actor="owner", reason="test: no goals")
        s.commit()
        cand = _candidate(s, svc.settings)
        assert cand is not None and cand["text"] == goal_suggest.QUESTION
        card = cand["subject"]["cards"][0]
        # the lead number where higher is plainly better, as this year's total (not DSO summed)
        body = card["change"]["body"]
        assert body["metric"] == "net_revenue" and body["direction"] == "at_least"
        assert body["aggregate"] == "fiscal_year_total"
        raise_questions(s, [cand])
        s.commit()
        qid = _goal_q(s).id
    assert qid
    chat = "/home?panel=othor&subject=questions"
    page = html.unescape(env.client.get(h + chat).text)
    assert goal_suggest.QUESTION in page and card["label"] in page
    _post(env, h, "/othor/apply", {"subject": "questions", "back": chat})
    with svc.sf() as s, tenant_scope(cid):
        goals = {r.key: r.body for r in service.active(s, ["goal"])}
        assert card["change"]["key"] in goals
        # a goal exists: the question closes
        refresh_goal_question(s, None, True)
        q = _goal_q(s)
        assert q.status == "answered"
        # every goal removed: tonight's suggestion opens it again
        refresh_goal_question(s, cand, False)
        assert q.status == "open"


def test_add_a_goal_then_suggest_a_goal_makes_one_without_a_model(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        gone = [service.retire("goal", r.key) for r in service.active(s, ["goal"])]
        service.apply(s, gone, actor="owner", reason="test: no goals")
        s.commit()
    on = html.unescape(env.client.get(h + "/ontology").text)
    assert on.count("Add a goal") == 1 and "Suggest a goal" not in on  # one button (owner)
    back = "/ontology?panel=othor&subject=ontology:goal"
    page = html.unescape(env.client.get(h + back).text)
    assert page.index("Suggest a goal") < page.index('id="oc-reply"')  # above the box

    def no_model(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("Suggest a goal needs no model")

    from othor.ai.gateway import ModelGateway

    monkeypatch.setattr(ModelGateway, "complete", no_model)
    _post(env, h, "/othor/reply", {"subject": "ontology:goal", "suggest": "1", "back": back})
    page = html.unescape(env.client.get(h + back).text)
    assert "I suggested the number, the target and the date from your numbers." in page
    assert "Net revenue at least ₹" in page and "for the year to 31 Mar 2027" in page
    _post(env, h, "/othor/apply", {"subject": "ontology:goal", "back": back})
    with svc.sf() as s, tenant_scope(cid):
        assert any(r.body["metric"] == "net_revenue" for r in service.active(s, ["goal"]))
