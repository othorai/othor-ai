# SPDX-License-Identifier: Apache-2.0
"""0.29.4 (owner, 20 Nov 2026): a new brief shows real content at once. On Create its first issue
is assembled from the latest sealed run's own files with the run's own function, stored as made
at creation, and is exactly what the night makes for that brief on the same files. The run's
replay doesn't change; the 06:00 delivery doesn't send it; Send now sends it once. Needs
`make dev && make seed`."""

from __future__ import annotations

import html
import re

import sqlalchemy as sa

from othor.briefs import first_issue, service, step
from othor.briefs.models import Brief, BriefIssue
from othor.core.tenancy import tenant_scope
from othor.engine.ledger import sha
from othor.engine.models import Run
from othor.engine.replay import replay
from othor.engine.store import RunFiles, read_json, read_points, with_run_inputs
from othor.mail.models import Delivery
from othor.ontology.compiler.api import canonical
from othor.ontology.models import Spec
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_run_again import _briefs_sent, _work, env_ctx
from tests.web.test_sweep_web import _post, night  # noqa: F401


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _create(env, h: str, question: str = "Are my top 4 accounts still buying?") -> str:  # type: ignore[no-untyped-def]
    tok = env.csrf(h + "/brief")
    page = env.client.post(h + "/brief/new", data={"csrf": tok, "question": question}).text
    draft = html.unescape(re.search(r'name="draft" value="([^"]*)"', page).group(1))  # type: ignore[union-attr]
    r = env.client.post(
        h + "/brief/new/create", data={"csrf": tok, "draft": draft}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/brief?created=")
    return r.headers["location"]


def _new(svc, cid):  # type: ignore[no-untyped-def]
    with svc.sf() as s, tenant_scope(cid):
        return s.scalars(sa.select(Brief).where(Brief.is_default.is_(False))).one().id


def _night_issue(svc, cid, rid, bid) -> dict:  # type: ignore[no-untyped-def]
    """What the night makes for brief `bid` on run `rid`'s files: `step.make` with the brief
    among the night's own briefs, as tonight's snapshot would read it."""
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        spec = dict(s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one().compiled)
        b = s.scalars(sa.select(Brief).where(Brief.id == bid)).one()
        v = service.current(s, b)
        assert v is not None
        row = {**service.version_row(s, b, v, day=run.run_date), "previous": []}
        files = RunFiles(svc.settings.runs_dir(cid), rid)
        night_ctx = with_run_inputs(read_json(files.inputs / "context.json") or {}, files)
        night_ctx = {**night_ctx, "briefs": [*night_ctx["briefs"], row]}
        doc = step.make(
            spec,
            run.run_date,
            night_ctx,
            read_json(files.outputs / "findings.json") or [],
            read_json(files.outputs / "watches.json") or [],
            read_json(files.outputs / "forecasts.json") or {},
            read_points(files.series),
            read_json(files.outputs_json) or {},
        )
    return next(i["issue"] for i in doc["issues"] if i["brief_id"] == str(bid))


def test_a_new_brief_has_its_first_issue_at_once_and_it_is_the_nights(night):  # noqa: F811
    env, h, cid, rid, svc = night
    files = RunFiles(svc.settings.runs_dir(cid), rid)
    before = (files.outputs / step.BRIEFS).read_bytes()

    where = _create(env, h)
    bid = _new(svc, cid)
    with svc.sf() as s, tenant_scope(cid):
        issue = s.scalars(sa.select(BriefIssue).where(BriefIssue.brief_id == bid)).one()
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        assert issue.status == "created" and issue.run_id == rid and issue.date == run.run_date
        assert issue.prose == {"source": "template"}  # no model
        stored, stored_hash, version = dict(issue.body), issue.issue_hash, issue.brief_version
        # reproducible from the same run files plus the brief version
        b = s.scalars(sa.select(Brief).where(Brief.id == bid)).one()
        again = first_issue.remake(s, svc.settings, b, run, version)
        assert sha(canonical(again).encode()) == stored_hash
    # the deterministic match: what the night makes for this brief on the same files
    assert canonical(_night_issue(svc, cid, rid, bid)) == canonical(stored)
    made = env.events("brief.issue_made_at_creation")
    assert len(made) == 1 and made[0].after["hash"] == stored_hash

    # the run is untouched: its files and its replay
    assert (files.outputs / step.BRIEFS).read_bytes() == before
    with tenant_scope(cid):
        assert replay(env_ctx(svc, env), rid).ok

    # the list: the new card first, with the issue's headline; the toast as in 0.29.1
    listing = _text(env.client.get(h + where).text)
    assert "created · first brief tomorrow 6:00 AM" in listing and "Undo" in listing
    assert "From last night" in listing and stored["title"] in listing
    assert "First issue at" not in listing

    page = _text(env.client.get(h + f"/brief/{bid}").text)
    assert "From last night’s results · first scheduled issue tomorrow 6:00 AM" in page
    assert "No issue yet" not in page and stored["title"] in page
    assert "Send now" in page  # the owner is its one recipient; email is on
    # nothing is sent on its own
    assert not _briefs_sent(env)


def test_send_now_delivers_once_and_the_morning_does_not_resend(night):  # noqa: F811
    from othor.core.models import Company
    from othor.mail.delivery import deliver_brief

    env, h, cid, rid, svc = night
    _create(env, h)
    bid = _new(svc, cid)
    with svc.sf() as s, tenant_scope(cid):
        issue = s.scalars(sa.select(BriefIssue).where(BriefIssue.brief_id == bid)).one()
        iid = issue.id
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        company = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        # the morning's delivery of that run skips an issue made at creation
        out = deliver_brief(
            s, svc.settings, svc.key, svc.relay, company, bid, run.run_date, env.clock.t
        )
        assert out.status == "no_issue"
        s.rollback()

    r = _post(env, h, f"/brief/{bid}/issues/{iid}/send")
    assert r.status_code == 303
    assert len(env.events("brief.send_now_requested")) == 1
    assert "Sending to 1 recipient…" in _text(env.client.get(h + f"/brief/{bid}").text)
    _work(env, svc)
    assert len(_briefs_sent(env)) == 1
    with svc.sf() as s, tenant_scope(cid):
        rows = s.scalars(sa.select(Delivery)).all()
        assert [(d.brief_issue_id, d.status) for d in rows] == [(iid, "sent")]
    page = _text(env.client.get(h + f"/brief/{bid}").text)
    assert "Sent to 1 of 1 recipient" in page and "Send now" not in page
    # a second press sends nothing more
    _post(env, h, f"/brief/{bid}/issues/{iid}/send")
    _work(env, svc)
    assert len(_briefs_sent(env)) == 1
    assert len(env.events("brief.send_now_requested")) == 1


def test_before_a_sealed_run_the_brief_says_when_its_first_issue_comes(night):  # noqa: F811
    env, h, cid, rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        run.status = "failed"  # no sealed run to make it from
        base = s.scalars(sa.select(Brief)).one()
        cur = service.current(s, base)
        assert cur is not None
        b = service.create(s, {**cur.body, "name": "Customer retention"}, actor="test")
        assert first_issue.at_creation(s, svc.settings, b, actor="test", now=env.clock.t) is None
        s.commit()
        bid = b.id
        assert not s.scalars(sa.select(BriefIssue).where(BriefIssue.brief_id == bid)).all()
    page = _text(env.client.get(h + f"/brief/{bid}").text)
    assert "No issue yet The first one arrives tomorrow at 6:00 AM" in page
    assert "From last night’s results" not in page
    assert not env.events("brief.issue_made_at_creation")
