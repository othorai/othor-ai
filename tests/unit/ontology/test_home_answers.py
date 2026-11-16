# SPDX-License-Identifier: Apache-2.0
"""Answers on Home (3aa, M10a): quick replies map to typed records with no model; free text is
drafted, validated and guarded; Skip comes back after a week; Undo puts the records back."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa

from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.home import answers
from othor.ontology import service
from othor.ontology.models import OntologyRecord, Question

TODAY = dt.date(2026, 11, 2)
NOW = dt.datetime(2026, 11, 2, 4, 0, tzinfo=dt.UTC)


def _q(s, kind, subject, replies=(), rank="2|", text="What happened with Ravira Exports?"):
    q = Question(
        text=text,
        why="",
        origin={},
        quick_replies=list(replies),
        fingerprint=uuid.uuid4().hex,
        kind=kind,
        subject=subject,
        rank_key=rank,
    )
    s.add(q)
    s.flush()
    return q


def _rec(s, q):
    return s.scalars(sa.select(OntologyRecord).where(OntologyRecord.id == q.answer_record_id)).one()


RAVIRA = {"entity": "Ravira Exports", "since": "2026-10-09", "finding": "fpK", "money": "9"}


def test_a_pause_takes_two_replies_and_undo_reopens_it(world):
    with world.sf() as s, tenant_scope(world.cid):
        q = _q(s, "not_determinable", RAVIRA, ["Yes, expected", "No, look into it"])
        n = service.current_number(s)
        out = answers.reply(s, q, "Yes, expected", actor="o", now=NOW, today=TODAY)
        assert out.follow_up == list(answers.UNTIL) and not out.saved and q.status == "open"
        out = answers.reply(s, q, "End of next month", actor="o", now=NOW, today=TODAY)
        assert out.saved and q.status == "answered" and q.answer_version == n + 1
        r = _rec(s, q)
        assert r.body["window"] == {
            "kind": "range",
            "start": "2026-10-09",
            "end": "2026-12-31",
            "dates": [],
        }
        assert r.body["entity"] == "Ravira Exports" and r.body["answered_on"] == "2026-11-02"
        assert answers.record_line(r) == "Known pause: Ravira Exports, 9 Oct – 31 Dec"
        assert answers.confirmation(q, r) == "Sweep won't flag Ravira Exports until 31 Dec."
        answers.undo(s, q, actor="o")
        assert q.status == "open" and service.current_number(s) == n + 2
        assert not any(x.key == "pause_ravira_exports" for x in service.active(s))
        acts = {e.action for e in s.scalars(sa.select(Event))}
        assert {"question.answered", "question.undone", "ontology.undone"} <= acts


def test_confirming_a_pattern_and_naming_a_plant(world):
    with world.sf() as s, tenant_scope(world.cid):
        q = _q(s, "pattern", {"pattern": "surat_shutdown"}, rank="3|")
        answers.reply(s, q, "Yes, every year", actor="o", now=NOW, today=TODAY)
        assert _rec(s, q).body["confirmed"] is True
        u = _q(s, "unmapped_plant", {"metric": "net_revenue", "prefix": "KO-"}, rank="5|")
        out = answers.reply(s, u, "Another plant", actor="o", now=NOW, today=TODAY)
        assert out.note and u.status == "open"
        out = answers.free_text(
            s, u, "Kochi", actor="o", now=NOW, today=TODAY, draft=lambda i: None
        )
        assert out.saved and _rec(s, u).body["entries"]["KO-"] == "Kochi"


def test_a_side_is_kept_as_the_answer(world):
    with world.sf() as s, tenant_scope(world.cid):
        sub = {"metric": "net_revenue", "sides": ["sales_dw", "erp_db"]}
        q = _q(s, "needs_confirmation", sub, rank="1|")
        answers.reply(s, q, "erp_db is right", actor="o", now=NOW, today=TODAY)
        assert q.answer_text == "erp_db" and _rec(s, q).type == "answer"


def test_skip_comes_back_after_a_week_and_the_queue_is_ordered(world):
    with world.sf() as s, tenant_scope(world.cid):
        a = _q(s, "pattern", {"pattern": "x"}, rank="3|")
        b = _q(s, "needs_confirmation", {"sides": []}, rank="1|")
        assert answers.queue(s, TODAY) == [b, a]
        answers.skip(s, b, actor="o", today=TODAY)
        assert answers.queue(s, TODAY) == [a]
        assert answers.queue(s, TODAY + dt.timedelta(days=7)) == [b, a]


def test_free_text_is_drafted_validated_and_guarded(world):
    with world.sf() as s, tenant_scope(world.cid):
        q = _q(s, "not_determinable", RAVIRA)
        good = {
            "record": {
                "type": "known_pattern",
                "body": {
                    "name": "Ravira Exports paused for an audit",
                    "window": {"kind": "range", "start": "2026-10-09", "end": "2026-12-01"},
                    "entity": "Ravira Exports",
                },
            }
        }
        seen = {}

        def draft(inputs):
            seen.update(inputs)
            return good

        unsure = {**good, "record": {**good["record"], "body": {**good["record"]["body"]}}}
        unsure["record"]["body"]["name"] = "Paused, 37 orders held"  # a number not in the input
        out = answers.free_text(
            s, q, "Paused until December", actor="o", now=NOW, today=TODAY, draft=lambda i: unsure
        )
        assert out.note == answers.NOT_AVAILABLE and q.status == "open"
        out = answers.free_text(
            s, q, "No model", actor="o", now=NOW, today=TODAY, draft=lambda i: None
        )
        assert out.note == answers.NOT_AVAILABLE
        out = answers.free_text(
            s,
            q,
            "Paused for an audit, back in December",
            actor="o",
            now=NOW,
            today=TODAY,
            draft=draft,
        )
        # drafted, not applied: nothing changes until the owner applies it (non-negotiable 6)
        assert out.drafted and not out.saved and q.status == "open" and q.answer_record_id is None
        assert q.draft and q.draft["changes"][0]["key"] == "pause_ravira_exports"
        assert answers.draft_lines(s, q) == ["Known pause: Ravira Exports, 9 Oct – 1 Dec"]
        n = service.current_number(s)
        assert answers.apply_draft(s, q, actor="o", now=NOW)
        assert q.status == "answered" and q.draft is None and service.current_number(s) == n + 1
        assert _rec(s, q).key == "pause_ravira_exports"
        # a count in a drafted name that the owner never wrote is refused (dates are allowed)
        q2 = _q(s, "not_determinable", {**RAVIRA, "entity": "Tessora EU"})
        counted = {**good, "record": {**good["record"], "body": {**good["record"]["body"]}}}
        counted["record"]["body"].update(entity="Tessora EU", name="12 orders held until 1 Dec")
        out = answers.free_text(
            s, q2, "held until December", actor="o", now=NOW, today=TODAY, draft=lambda i: counted
        )
        assert out.note == answers.NOT_AVAILABLE
        counted["record"]["body"]["name"] = "Tessora EU held until 1 Dec"
        out = answers.free_text(
            s, q2, "held until December", actor="o", now=NOW, today=TODAY, draft=lambda i: counted
        )
        assert out.drafted and q2.status == "open"
        # Edit: the draft goes and its text comes back; nothing was written
        assert answers.discard_draft(s, q2, actor="o") == "held until December"
        assert q2.draft is None and q2.status == "open"
        assert "money" not in seen["subject"]  # no figures go to the model
        wrong = _q(s, "not_determinable", {**RAVIRA, "entity": "Tessora EU"})
        out = answers.free_text(
            s, wrong, "x", actor="o", now=NOW, today=TODAY, draft=lambda i: good
        )
        assert out.note == answers.NOT_AVAILABLE  # a draft about another customer is refused


def test_a_set_aside_question_stays_counted_and_answerable(world):
    """Ask me later hides nothing: the question is in set_aside() and in the panel's order."""
    import datetime as dt

    with world.sf() as s, tenant_scope(world.cid):
        q = _q(s, "pattern", {"pattern": "surat_shutdown"}, rank="3|")
        answers.skip(s, q, actor="o", today=TODAY - dt.timedelta(days=2))
        s.flush()
        assert q in answers.set_aside(s, TODAY)
        assert q in answers.todays_order(s, TODAY, dt.UTC)
        assert q not in answers.queue(s, TODAY)  # not open: it isn't nagged for
