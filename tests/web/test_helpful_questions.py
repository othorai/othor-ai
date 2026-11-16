# SPDX-License-Identifier: Apache-2.0
"""Helpful questions and Let Othor AI decide (v0.12, docs/plans/helpful-questions.md,
docs/plans/let-othor-ai-decide.md) on the steady-state night of Sun 1 Nov 2026.

The pair question (Lead/lag couldn't run: the sample has no "leads" pair) carries suggested
pairs found by the nightly questions step, each with its computed reason; the panel shows them
as radio cards in the owner's words, with one plain line on the check and no count of Sweep
tests (#33), and no "Drafts only" note (#33, option C). Picking a card drafts its record
(Draft · not applied yet), Apply writes one version. Let Othor AI decide applies the top card
when it clears the bar, tagged "Decided by Othor AI", with its event, capped confidence in the
spec, a row on the Ontology to Confirm, and Home's quiet count; below the bar it says why and the
next step and writes nothing. A question with nothing found says so and gives the next step.
The real read-only counts (orders against invoices, the status column, the limit column) run
through the source's query log."""

from __future__ import annotations

import copy
from typing import ClassVar

import sqlalchemy as sa

from othor.connectors.models import RunQuery
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.engine.context import EngineContext
from othor.engine.models import Run
from othor.engine.sources import RunInfo
from othor.engine.store import RunFiles
from othor.ontology import service
from othor.ontology import suggest as sg
from othor.ontology.compiler import compile_version
from othor.ontology.compiler.catalog import load_catalog
from othor.ontology.models import Question, Spec
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_in_place import INPLACE, _post
from tests.web.test_sweep_web import night  # noqa: F401

NOTE = "Drafts only. Numbers are computed, not written. Nothing changes until you apply."


def _pair_q(svc, cid) -> Question:
    with svc.sf() as s, tenant_scope(cid):
        q = s.scalars(sa.select(Question).where(Question.kind == "needs")).one()
        s.expunge(q)
        return q


def _panel(env, h, qid) -> str:
    page = env.client.get(f"{h}/home?panel=questions&q={qid}").text
    return page.split('id="chat-panel"', 1)[1]


def _events(svc, cid, action) -> list[Event]:
    with svc.sf() as s, tenant_scope(cid):
        return list(s.scalars(sa.select(Event).where(Event.action == action)))


def test_the_pair_question_shows_cards_with_computed_reasons(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    q = _pair_q(svc, cid)
    sub = q.subject or {}
    cards = sub["suggestions"]
    assert sub["needs"] == "pair" and 1 <= len(cards) <= 3
    # the nightly step stored them: code found them, each with its computed evidence
    pool = {(p["a"], p["b"]): p for p in sub["pool"]}
    assert ("freight", "metres_dispatched") not in pool  # already a pair in the Ontology
    for c in cards:
        a, b = c["change"]["body"]["over"]
        ev = pool[(a, b)]
        assert c["sub"].startswith("Moved the same way in ") or " ahead; " in c["sub"]
        assert f" {ev['agree']} of the last {ev['weeks']} weeks" in c["sub"]
    panel = _panel(env, h, q.id)
    assert "Should these two numbers move together?" in panel
    assert "Sweep watches pairs that should move together" in panel
    assert "Sweep test" not in panel and "could not run last night" not in panel  # #33
    assert NOTE not in panel  # #33, option C: the answers here are the owner's
    assert "Suggested from your data" in panel
    for c in cards:
        assert f'value="s:{c["key"]}"' in panel and c["label"] in panel and c["sub"] in panel
    assert "Something else…" in panel and "Let Othor AI decide" in panel
    assert f'formaction="/home/questions/{q.id}/decide"' in panel
    assert "Ask me later" in panel and "We don’t have this" in panel


def test_picking_a_card_drafts_it_and_apply_writes_one_version(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    q = _pair_q(svc, cid)
    card = (q.subject or {})["suggestions"][0]
    with svc.sf() as s, tenant_scope(cid):
        before = service.current_number(s)
    r = _post(
        env,
        h,
        f"/home/questions/{q.id}/answer",
        {"pick": "s:" + card["key"]},
        follow_redirects=True,
        headers=INPLACE,
    )
    assert "Draft · not applied yet" in r.text and f"You picked: {card['label']}" in r.text
    assert card["change"]["body"]["text"] in r.text
    with svc.sf() as s, tenant_scope(cid):
        assert service.current_number(s) == before  # nothing changes until Apply
    r = _post(env, h, f"/home/questions/{q.id}/apply", follow_redirects=True, headers=INPLACE)
    assert "You answered" in r.text and "Sweep watches these two numbers together" in r.text
    assert "Decided by Othor AI" not in r.text  # the owner's own pick
    with svc.sf() as s, tenant_scope(cid):
        assert service.current_number(s) == before + 1
        rec = next(x for x in service.active(s, ["rule"]) if x.key == card["change"]["key"])
        assert rec.body["kind"] == "pair" and "decided_by" not in (rec.source_ref or {})
    assert _events(svc, cid, "question.drafted") and _events(svc, cid, "question.answered")


def test_let_othor_ai_decide_applies_tagged_capped_and_listed(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    q = _pair_q(svc, cid)
    pick = (q.subject or {})["decide"]["pick"]
    assert pick, "the sample's strongest pair clears the bar"
    card = next(c for c in q.subject["suggestions"] if c["key"] == pick)
    with svc.sf() as s, tenant_scope(cid):
        before = service.current_number(s)
    r = _post(env, h, f"/home/questions/{q.id}/decide", follow_redirects=True, headers=INPLACE)
    assert "Decided by Othor AI" in r.text and card["sub"] in r.text
    assert "Confidence capped at Medium until you confirm it" in r.text
    with svc.sf() as s, tenant_scope(cid):
        assert service.current_number(s) == before + 1
        rec = next(x for x in service.active(s, ["rule"]) if x.key == card["change"]["key"])
        ref = rec.source_ref or {}
        assert ref["decided_by"] == "othor_ai" and ref["confidence"] == "medium"
        assert ref["evidence"] == card["sub"] and ref["answer"] == str(q.id)
        spec = compile_version(s, _company(s, cid))
        assert f"pair:{rec.key}" in spec.compiled["decided_by_ai"]  # findings capped
        rid = str(rec.id)
        s.commit()
    (ev,) = _events(svc, cid, "question.decided_by_ai")
    assert ev.after["suggestion"] == pick and ev.after["confidence"] == "medium"
    # Home's quiet count, and the Ontology's group to confirm or fix
    home = env.client.get(h + "/home").text
    assert "1 decision by Othor AI to check" in home
    page = env.client.get(h + "/ontology").text
    assert 'id="decided"' in page and "Decided by Othor AI" in page and card["sub"] in page
    assert f'action="/ontology/decided/{rid}/confirm"' in page
    assert f'action="/home/questions/{q.id}/undo"' in page
    # Confirm: the owner's now, the tag goes, the spec drops the cap
    r = _post(env, h, f"/ontology/decided/{rid}/confirm", follow_redirects=True, headers=INPLACE)
    assert 'id="decided"' not in r.text
    with svc.sf() as s, tenant_scope(cid):
        assert service.current_number(s) == before + 2
        rec = next(x for x in service.active(s, ["rule"]) if x.key == card["change"]["key"])
        assert "decided_by" not in (rec.source_ref or {})
        spec = compile_version(s, _company(s, cid))
        assert "decided_by_ai" not in spec.compiled
    assert _events(svc, cid, "ontology.decision_confirmed")


def _company(s, cid):
    from othor.core.models import Company

    return s.scalars(sa.select(Company).where(Company.id == cid)).one()


def test_below_the_bar_it_says_why_and_the_next_step_and_writes_nothing(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    q = _pair_q(svc, cid)
    with svc.sf() as s, tenant_scope(cid):
        row = s.get(Question, q.id)
        sub = copy.deepcopy(row.subject)
        for c in sub["suggestions"]:
            c["strong"] = False
        sub["decide"] = {
            "pick": None,
            "why": "No pair is steady enough to pick for you: the strongest moved the same way "
            "in 30 of 52 weeks; Othor AI needs 39.",
            "next": "Pick one yourself if it makes sense for your business.",
            "href": "",
        }
        row.subject = sub
        before = service.current_number(s)
        s.commit()
    r = _post(env, h, f"/home/questions/{q.id}/decide", follow_redirects=True, headers=INPLACE)
    assert "Othor AI can’t decide this one yet" in r.text
    assert sub["decide"]["why"] in r.text and sub["decide"]["next"] in r.text
    assert sub["suggestions"][0]["label"] in r.text  # the cards stay to pick from
    with svc.sf() as s, tenant_scope(cid):
        assert service.current_number(s) == before
        assert s.get(Question, q.id).status == "open"
    assert _events(svc, cid, "question.decide_declined")


def test_nothing_found_says_so_with_the_next_step(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        f = sg.entities(_EmptyCat(), {"sources": {}, "metrics": {}}, {}, {})
        q = Question(
            text=sg.QUESTION["entity"],
            why=sg.EXPLAIN["entity"],
            fingerprint="needs-entity-test",
            kind="needs",
            subject={"needs": "entity", "detectors": ["missing_records"], **f.as_subject()},
            quick_replies=["We don't have this"],
            origin={"rule": "detector_needs", "needs": "entity"},
            unblocks={"needs": "entity", "detectors": ["missing_records"]},
            rank_key="0|",
        )
        s.add(q)
        s.commit()
        qid = q.id
    panel = _panel(env, h, qid)
    assert "Is this your customer list, with who is active?" in panel
    assert "Nothing found yet" in panel and f.why in panel
    assert 'href="/sources"' in panel and f.next in panel
    assert 'value="s:' not in panel  # no empty cards
    assert "Your answer" in panel  # the box, to say it in their own words


class _EmptyCat:
    tables: ClassVar[dict] = {}


def test_the_counts_are_read_only_sql_through_the_query_log(night):  # noqa: F811
    """The real path: orders in sales_dw against invoices in erp_db, the customers' status
    column and the approvals' limit column, counted on the sample databases, logged."""
    from othor.engine import suggest_facts

    env, _h, cid, rid, svc = night
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    with svc.sf() as s, tenant_scope(cid):
        run = s.get(Run, rid)
        spec = copy.deepcopy(s.get(Spec, run.spec_id).compiled)
        cat = load_catalog(s)
        records = {(r.type, r.key): dict(r.body) for r in service.active(s)}
        info = RunInfo(
            run.id,
            cid,
            run.number,
            run.kind,
            run.run_date,
            spec,
            RunFiles(svc.settings.runs_dir(cid), run.id),
        )
        logged = s.scalar(sa.select(sa.func.count()).select_from(RunQuery))
    # as if the sample had no reconcile rule, no limit rule and no customer list yet
    spec["probes"] = {**spec["probes"], "reconcile": [], "thresholds": []}
    b = dict(records[("breakdown", "customer")])
    b.pop("entity")
    records[("breakdown", "customer")] = b
    with tenant_scope(cid):
        facts = suggest_facts.facts_for(
            ctx, info, {"reconcile", "entity", "threshold"}, cat, records
        )
    got = facts["reconcile"]["sales_dw.fact_order.order_no>erp_db.invoices.order_ref"]
    assert 0 < got["found"] <= got["left"]
    f = sg.reconciles(cat, spec, facts["reconcile"])
    assert any("order numbers in sales_dw are in erp_db" in c.sub for c in f.cards)
    (ent,) = facts["entity"].values()
    assert "active" in [v for v, _n in ent["values"]] and ent["rows"] > 0
    (lim,) = facts["threshold"].values()
    assert float(lim["values"][0][0]) == 500000.0
    with svc.sf() as s, tenant_scope(cid):
        rows = list(s.scalars(sa.select(RunQuery)))
        assert len(rows) > logged  # every count is in the run's query log
        for q in rows[logged:]:
            low = q.sql.lower()
            assert "contact_email" not in low and "contact_phone" not in low
            assert low.lstrip().startswith("select")
