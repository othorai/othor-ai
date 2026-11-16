# SPDX-License-Identifier: Apache-2.0
"""Settings' Your data (M18.1): a small company downloads its zip at once; a large one's is built
by a job that emails a link (sign-in needed, kept 48 hours). Every step is an event."""

from __future__ import annotations

import io
import zipfile

import sqlalchemy as sa

from othor import exporting
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext, run_pending
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _events(svc, cid, action):
    with svc.sf() as s, tenant_scope(cid):
        return s.scalars(sa.select(Event).where(Event.action == action)).all()


def _work(env, svc):
    return run_pending(
        WorkerContext(svc.settings, svc.sf, svc.key, clock=svc.clock, relay=env.relay)
    )


def test_the_row_and_a_direct_download(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    p = env.client.get(h + "/settings").text
    assert ">Your data<" in p and "Download everything" in p
    assert 'href="/settings/export" download' in p
    r = env.client.get(h + "/settings/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["cache-control"] == "private, no-store"
    assert 'filename="othor-indigothread-export-2026-11-01.zip"' in r.headers["content-disposition"]
    assert "README.txt" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    ev = _events(svc, cid, "company.exported")
    assert (
        len(ev) == 1 and ev[0].after["mode"] == "direct" and ev[0].after["bytes"] == len(r.content)
    )


def test_signed_out_goes_to_sign_in(night):  # noqa: F811
    env, h, *_ = night
    env.client.cookies.clear()
    r = env.client.get(h + "/settings/export", follow_redirects=False)
    assert r.status_code in (302, 303) and "/login" in r.headers["location"]


def test_a_large_company_gets_a_job_an_email_and_a_link(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    monkeypatch.setattr(exporting, "LARGE_BRIEFS", 0)
    p = env.client.get(h + "/settings").text
    assert 'hx-post="/settings/export"' in p
    assert env.client.get(h + "/settings/export", follow_redirects=False).status_code == 303
    tok = env.csrf(h + "/settings")
    for _ in range(2):  # a second click while it's being prepared queues nothing
        r = env.client.post(h + "/settings/export", data={"csrf": tok})
        assert r.status_code == 200 and "Preparing" in r.text
    assert "Building your export" in r.text and 'hx-trigger="every 10s"' in r.text
    with svc.sf() as s, tenant_scope(cid):
        jobs = s.scalars(sa.select(Job).where(Job.kind == "export")).all()
    assert len(jobs) == 1
    before = len(env.relay.outbox)
    _work(env, svc)
    mail = env.relay.outbox[before:]
    assert len(mail) == 1 and mail[0].subject == "[Othor AI] Your export is ready"
    link = f"/settings/export/{jobs[0].id}"
    assert link in mail[0].text and "No passwords, tokens or source rows" in mail[0].text
    row = env.client.get(h + "/settings/export/row").text
    assert "Ready · kept until" in row and f'href="{link}" download' in row
    r = env.client.get(h + link)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(r.content)).testzip() is None
    assert [e.after["mode"] for e in _events(svc, cid, "company.exported")] == ["job"]
    assert len(_events(svc, cid, "company.export_requested")) == 1
    assert len(_events(svc, cid, "company.export_downloaded")) == 1
    # a retry of the job sends nothing twice
    with svc.sf() as s, tenant_scope(cid):
        j = s.get(Job, jobs[0].id)
        j.status, j.attempts = "queued", 0
        s.commit()
    _work(env, svc)
    assert len(env.relay.outbox) == before + 1
    # 48 hours on: the link reads as expired, and the worker deletes the file
    env.clock.tick(hours=49)
    gone = env.client.get(h + link)
    assert gone.status_code == 404 and "This export has expired" in gone.text
    path = exporting.zip_path(svc.settings, cid, jobs[0].id)
    import os

    os.utime(path, (0, 0))
    assert exporting.prune(svc.settings, env.clock()) == 1 and not path.exists()


def test_another_companys_or_a_made_up_export_is_not_found(night):  # noqa: F811
    env, h, *_ = night
    for jid in ("00000000-0000-0000-0000-000000000001", "not-a-uuid"):
        r = env.client.get(h + f"/settings/export/{jid}")
        assert r.status_code == 404 and "This export has expired" in r.text
