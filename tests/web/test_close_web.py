# SPDX-License-Identifier: Apache-2.0
"""Close the company (M18.2): the name, an emailed code, then Delete. Everything stops at once:
the host, sessions, jobs and credentials; support can reopen it within 30 days."""

from __future__ import annotations

import datetime as dt
import re

import pytest
import sqlalchemy as sa

from othor import closing
from othor.connectors.models import Source, SourceSecret
from othor.core.models import AuthSession, Company, Event, PlatformEvent
from othor.core.tenancy import system_scope, tenant_scope
from othor.jobs.models import Job
from othor.jobs.queue import enqueue
from othor.jobs.runner import WorkerContext, claim
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _code(env) -> str:
    m = next(m for m in reversed(env.relay.outbox) if "code to delete" in m.subject)
    return re.search(r"(\d{3}) (\d{3})", m.subject).group(1) + re.search(
        r"(\d{3}) (\d{3})", m.subject
    ).group(2)


def _post(env, h, path, data):
    tok = env.csrf(h + "/settings")
    return env.client.post(h + path, data={"csrf": tok, **data}, headers={"HX-Request": "true"})


def _digits(code: str) -> dict[str, str]:
    return {f"code{i}": d for i, d in enumerate(code)}


def test_the_section_and_a_wrong_name(night):  # noqa: F811
    env, h, *_ = night
    p = env.client.get(h + "/settings").text
    assert ">Delete company<" in p and "section-head danger" in p
    assert 'data-dialog-open="close-dlg"' in p
    assert "Type Indigo Thread Co. to confirm" in p
    before = len(env.relay.outbox)
    r = _post(env, h, "/settings/close/start", {"name": "Indigo Thread"})
    assert r.status_code == 200 and "That isn’t the company’s name" in r.text
    assert len(env.relay.outbox) == before  # no code sent


def test_a_wrong_code_then_the_right_one_closes_everything(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        enqueue(s, "scan", {"x": 1})
        s.commit()
        assert s.scalars(sa.select(SourceSecret)).first() is not None
    r = _post(env, h, "/settings/close/start", {"name": "  indigo thread co. "})
    assert r.status_code == 200 and "We sent a 6-digit code to u•••@" in r.text
    # the code never deletes by itself: Delete is a click (owner, 16 Nov 2026); sign-in still does
    assert "data-no-autosubmit" in r.text
    code = _code(env)
    wrong = "000000" if code != "000000" else "111111"
    r = _post(env, h, "/settings/close/confirm", _digits(wrong))
    assert r.status_code == 200 and "4 tries left" in r.text
    r = _post(env, h, "/settings/close/confirm", _digits(code))
    assert r.status_code == 200 and "/closed?t=" in r.headers["HX-Redirect"]
    closed = env.client.get(r.headers["HX-Redirect"]).text
    assert "Indigo Thread Co. is deleted" in closed and "To restore it before then" in closed
    mail = env.relay.outbox[-1]
    assert mail.subject == "[Othor AI] Indigo Thread Co. is deleted"
    # the host, the session, the jobs, the credentials
    assert env.client.get(h + "/settings", follow_redirects=False).status_code == 404
    with svc.sf() as s, system_scope("test"):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        assert c.deleted_at is not None and c.purge_after == c.deleted_at + closing.KEEP_FOR
        assert (
            s.scalars(
                sa.select(AuthSession).where(
                    AuthSession.company_id == cid, AuthSession.revoked_at.is_(None)
                )
            ).all()
            == []
        )
        assert s.scalars(sa.select(SourceSecret).where(SourceSecret.company_id == cid)).all() == []
        srcs = s.scalars(sa.select(Source).where(Source.company_id == cid)).all()
        assert srcs and {x.status for x in srcs if x.deleted_at is None} == {"disconnected"}
        jobs = s.scalars(sa.select(Job).where(Job.company_id == cid)).all()
        assert "queued" not in {j.status for j in jobs} and "cancelled" in {j.status for j in jobs}
        pe = s.scalars(sa.select(PlatformEvent).where(PlatformEvent.action == "company.closed"))
        assert len(pe.all()) == 1
        ev = s.scalars(
            sa.select(Event).where(Event.company_id == cid, Event.action == "company.closed")
        ).one()
        assert ev.after["sources"] == len([x for x in srcs])
    assert claim(WorkerContext(svc.settings, svc.sf, svc.key, clock=svc.clock)) is None
    assert svc.auth.session_for(None) is None


def test_reopen_within_30_days_and_not_after(night):  # noqa: F811
    _env, _h, cid, _rid, svc = night
    now = svc.clock()
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        closing.close(s, c, actor="test", now=now)
        s.commit()
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        with pytest.raises(closing.CloseError):
            closing.close(s, c, actor="test", now=now)
        with pytest.raises(closing.CloseError, match="past its purge date"):
            closing.reopen(s, c, actor="support", now=now + dt.timedelta(days=31))
        closing.reopen(s, c, actor="support", now=now + dt.timedelta(days=29))
        s.commit()
    with svc.sf() as s, system_scope("test"):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        assert c.deleted_at is None and c.purge_after is None and c.closed_by is None


def test_a_session_of_a_closed_company_signs_no_one_in(night):  # noqa: F811
    env, _h, cid, _rid, svc = night
    tok = next(c.value for c in env.client.cookies.jar if c.name == "othor_session")
    assert svc.auth.session_for(tok) is not None
    with svc.sf() as s, system_scope("test: close without ending sessions"):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        c.deleted_at = svc.clock()
        s.commit()
    assert svc.auth.session_for(tok) is None


def test_sign_in_codes_still_submit_by_themselves() -> None:
    from othor.web.templating import templates

    t = templates.env.from_string(
        '{% from "components/forms.html" import code_boxes %}{{ code_boxes() }}|'
        "{{ code_boxes(auto=False) }}"
    )
    sign_in, close = t.render().split("|")
    assert "data-no-autosubmit" not in sign_in and "data-no-autosubmit" in close
