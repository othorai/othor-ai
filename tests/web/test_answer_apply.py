# SPDX-License-Identifier: Apache-2.0
"""A typed answer waits for Apply (non-negotiable 6; v0.11 item 1, 12 Nov 2026): the panel shows
the drafted change with Apply and Edit; Apply writes it as one version; Edit puts the text back."""

from __future__ import annotations

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.ontology import service
from othor.ontology.models import Question
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_in_place import INPLACE, _post
from tests.web.test_sweep_web import night  # noqa: F401


def test_a_drafted_answer_shows_apply_and_edit_and_applies_once(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        q = s.scalars(
            sa.select(Question).where(
                Question.status == "open", Question.kind != "needs_confirmation"
            )
        ).first()  # a question that takes a typed answer
        assert q is not None
        q.draft = {
            "text": "Paused for an audit, back in December",
            "changes": [
                {
                    "op": "put",
                    "type": "known_pattern",
                    "key": "pause_test_customer",
                    "body": {
                        "name": "Test customer paused for an audit",
                        "window": {"kind": "range", "start": "2026-10-09", "end": "2026-12-01"},
                        "entity": "Test customer",
                    },
                }
            ],
        }
        qid, before = str(q.id), service.current_number(s)
        s.commit()
    page = env.client.get(f"{h}/home?panel=questions&q={qid}").text
    assert "Draft · not applied yet" in page and "Known pause: Test customer" in page
    # what was typed and what it will do (moved from test_questions_panel)
    assert "You wrote: “Paused for an audit, back in December”" in page
    assert "flag Test customer until 1 Dec." in page
    assert (
        f'action="/home/questions/{qid}/apply"' in page
        and f'action="/home/questions/{qid}/edit"' in page
    )
    # Edit: nothing written, the text back in the box
    r = _post(env, h, f"/home/questions/{qid}/edit", follow_redirects=True, headers=INPLACE)
    assert ">Paused for an audit, back in December</textarea>" in r.text
    with svc.sf() as s, tenant_scope(cid):
        assert service.current_number(s) == before
        q = s.get(Question, q.id)
        assert q.draft is None and q.status == "open"
        q.draft = {
            "text": "x",
            "changes": [
                {
                    "op": "put",
                    "type": "known_pattern",
                    "key": "pause_test_customer",
                    "body": {
                        "name": "Test customer paused",
                        "window": {"kind": "range", "start": "2026-10-09", "end": "2026-12-01"},
                        "entity": "Test customer",
                    },
                }
            ],
        }
        s.commit()
    r = _post(env, h, f"/home/questions/{qid}/apply", follow_redirects=True, headers=INPLACE)
    assert "You answered" in r.text  # in place
    with svc.sf() as s, tenant_scope(cid):
        q = s.get(Question, q.id)
        assert q.status == "answered" and q.draft is None
        assert service.current_number(s) == before + 1
