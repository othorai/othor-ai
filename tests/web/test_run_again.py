# SPDX-License-Identifier: Apache-2.0
"""Run again now (v0.11, owner 11 Nov 2026): the nightly steps on demand, as a new run of kind
`rerun` that replays byte for byte; each brief gets a new version of the day's issue (status
`rerun`) and the 06:00 one stays in the Archive; no email unless the owner sends it; the guards
(one run at a time, 3 a day, not 01:00–06:30, every request an event); and New/Worse, "Since
yesterday" and rank stability against the previous day's run, never an earlier run the same day.
Needs `make dev && make seed`."""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from othor.briefs.models import BriefIssue
from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.engine import again
from othor.engine.ledger import verify
from othor.engine.models import Finding, Run
from othor.engine.replay import replay
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext, run_pending
from othor.mail.models import Delivery
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import _post, night  # noqa: F401

UTC = dt.UTC
AT_1540 = dt.datetime(2026, 11, 1, 10, 10, tzinfo=UTC)  # 3:40 PM IST, Sun 1 Nov
AT_0300 = dt.datetime(2026, 10, 31, 21, 30, tzinfo=UTC)  # 3:00 AM IST, Sun 1 Nov


def _work(env, svc) -> None:  # type: ignore[no-untyped-def]
    run_pending(WorkerContext(svc.settings, svc.sf, svc.key, clock=svc.clock, relay=svc.relay))


def _brief(svc, cid):  # type: ignore[no-untyped-def]
    from othor.briefs import service

    with svc.sf() as s, tenant_scope(cid):
        return service.active(s)[0].id


def _briefs_sent(env) -> list:  # type: ignore[no-untyped-def]
    return [m for m in env.relay.outbox if "sign-in code" not in m.subject]


def _press(env, h, path: str):  # type: ignore[no-untyped-def]
    return _post(env, h, "/runs/again", {"next": path})


def test_the_guards_say_why_and_every_press_is_an_event(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid = _brief(svc, cid)
    page = f"/brief/{bid}"

    # 01:00–06:30 company time: the nightly run and the morning brief have the sources
    env.clock.t = AT_0300
    r = _press(env, h, page)
    assert r.status_code == 303 and r.headers["location"] == f"{page}?again=window"
    text = env.client.get(h + r.headers["location"]).text
    assert "Run again opens at 6:30 AM" in text
    refused = env.events("run.again_refused")
    assert [e.after["reason"] for e in refused] == ["window"]
    assert refused[0].actor and refused[0].actor != "system"

    # 15:40: it starts; the button waits and the run's stages show in place, polling
    env.clock.t = AT_1540
    r = _press(env, h, page)
    assert r.status_code == 303 and r.headers["location"] == page
    asked = env.events("run.again_requested")
    assert len(asked) == 1 and asked[0].after == {"date": "2026-11-01", "n": 1}
    text = env.client.get(h + page).text
    assert "Running again with every source" in text and 'hx-trigger="every 2s"' in text
    assert "Running…" in text

    # one run at a time
    r = _press(env, h, page)
    assert r.headers["location"] == f"{page}?again=running"
    assert "A run is running now" in env.client.get(h + r.headers["location"]).text
    assert [e.after["reason"] for e in env.events("run.again_refused")] == ["window", "running"]

    # three a day (by the company's local day): the jobs stand in for finished runs
    with svc.sf() as s, tenant_scope(cid):
        company = s.get(Company, cid)
        assert company is not None
        for n in (1, 2):
            for j in s.scalars(sa.select(Job).where(Job.dedupe_key.like("again:%"))):
                j.status = "done"
            out = again.request(s, company, "test", env.clock.t)
            assert isinstance(out, Job) and out.dedupe_key == f"again:2026-11-01:{n + 1}"
        for j in s.scalars(sa.select(Job).where(Job.dedupe_key.like("again:%"))):
            j.status = "done"
        out = again.request(s, company, "test", env.clock.t)
        assert isinstance(out, again.Refusal) and out.code == "limit"
        assert "3 times today" in out.text and "02:00" in out.text
        # the next local day starts again from none
        tomorrow = dt.datetime(2026, 11, 2, 4, 0, tzinfo=UTC)  # 9:30 AM IST, Mon 2 Nov
        assert again.refusal(s, company, tomorrow) is None
        s.rollback()


def test_a_rerun_remakes_the_brief_replays_and_compares_with_yesterday(night):  # noqa: F811
    env, h, cid, rid, svc = night
    bid = _brief(svc, cid)
    env.clock.t = AT_1540
    assert _press(env, h, f"/brief/{bid}").status_code == 303
    _work(env, svc)

    with svc.sf() as s, tenant_scope(cid):
        nightly = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        rerun = s.scalars(sa.select(Run).where(Run.kind == "rerun")).one()
        assert rerun.status == "done" and rerun.number == nightly.number + 1
        assert rerun.run_date == nightly.run_date  # the same day: a new version of its issue
        assert rerun.prev_run_hash == nightly.run_hash
        issues = s.scalars(sa.select(BriefIssue)).all()
        mine = {i.brief_id: i for i in issues if i.run_id == rerun.id}
        theirs = {i.brief_id: i for i in issues if i.run_id == nightly.id}
        assert mine and set(mine) == set(theirs)
        assert {i.status for i in mine.values()} == {"rerun"}
        assert {i.status for i in theirs.values()} == {"ready"}  # the 06:00 one stays
        # same sources, compared with the same previous day: the same brief, number for number
        # (against the 06:00 run it would rank by this morning's order and tag nothing New)
        for b, i in mine.items():
            assert i.issue_hash == theirs[b].issue_hash
        tags = {
            r: {
                f.fingerprint: (f.change, f.first_seen_run)
                for f in s.scalars(sa.select(Finding).where(Finding.run_id == run.id))
            }
            for r, run in (("n", nightly), ("r", rerun))
        }
        assert {k: v[0] for k, v in tags["n"].items()} == {k: v[0] for k, v in tags["r"].items()}
        assert any(ch == "New" for ch, _ in tags["r"].values())
        # first seen: by this run, not the 06:00 one (no run of an earlier day saw them)
        assert {n for _, n in tags["r"].values()} == {rerun.number}
        assert verify(s, svc.settings, cid).ok
        rerun_id = rerun.id
        rerun_number = rerun.number
        sent = s.scalar(sa.select(sa.func.count(Delivery.id)))
        deliver = s.scalars(sa.select(Job).where(Job.kind == "deliver")).all()
    with tenant_scope(cid):
        assert replay(env_ctx(svc, env), rerun_id).ok  # byte for byte
    # no email unless the owner sends it
    assert sent == 0 and not deliver and not _briefs_sent(env)

    page = env.client.get(h + f"/brief/{bid}").text
    assert f"Updated 3:40 PM · run #{rerun_number:04d}" in page
    assert "Send the updated brief to 1 recipient?" in page
    assert "Running again" not in page

    # the Archive keeps the 06:00 issue, and opens it
    archive = env.client.get(h + "/brief/archive").text
    assert f"#{rerun_number:04d} · updated" in archive
    with svc.sf() as s, tenant_scope(cid):
        early = s.scalars(
            sa.select(BriefIssue).where(BriefIssue.run_id == rid, BriefIssue.brief_id == bid)
        ).one()
    assert f"issue={early.id}" in archive
    # one row for the day (owner, 16 Nov 2026): the 06:00 issue opens from its "1 earlier run"
    assert "1 earlier run" in archive and archive.count("Sun 1 Nov") == 1
    old = env.client.get(h + f"/brief/{bid}?date=2026-11-01&issue={early.id}").text
    assert "Updated 3:40 PM" not in old and "6:00 AM" in old

    # Home: the button, and its words for the run
    home = env.client.get(h + "/home").text
    # the strip's (i) says when its checks ran: today, for a rerun
    assert "Refresh everything" in home and "could run on your data today" in home


def test_the_owner_sends_the_update_or_keeps_it(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid = _brief(svc, cid)
    env.clock.t = AT_1540
    _press(env, h, f"/brief/{bid}")
    _work(env, svc)
    with svc.sf() as s, tenant_scope(cid):
        issue = s.scalars(
            sa.select(BriefIssue).where(BriefIssue.status == "rerun", BriefIssue.brief_id == bid)
        ).one()
        iid = issue.id

    r = _post(env, h, f"/brief/{bid}/issues/{iid}/send")
    assert r.status_code == 303
    assert "Sending the updated brief to 1 recipient" in env.client.get(h + f"/brief/{bid}").text
    assert len(env.events("brief.update_send_requested")) == 1
    _work(env, svc)
    assert len(_briefs_sent(env)) == 1
    with svc.sf() as s, tenant_scope(cid):
        rows = s.scalars(sa.select(Delivery)).all()
        assert [(d.brief_issue_id, d.status) for d in rows] == [(iid, "sent")]
    page = env.client.get(h + f"/brief/{bid}").text
    assert "Sent the updated brief" in page and "Send the updated brief to" not in page
    # a second press sends nothing more
    _post(env, h, f"/brief/{bid}/issues/{iid}/send")
    _work(env, svc)
    assert len(_briefs_sent(env)) == 1


def test_not_now_keeps_the_update_unsent(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid = _brief(svc, cid)
    env.clock.t = AT_1540
    _press(env, h, f"/brief/{bid}")
    _work(env, svc)
    with svc.sf() as s, tenant_scope(cid):
        iid = s.scalars(sa.select(BriefIssue.id).where(BriefIssue.status == "rerun")).first()
    assert _post(env, h, f"/brief/{bid}/issues/{iid}/keep").status_code == 303
    page = env.client.get(h + f"/brief/{bid}").text
    assert "Send the updated brief" not in page and "Updated 3:40 PM" in page
    assert len(env.events("brief.update_kept")) == 1 and not _briefs_sent(env)


def env_ctx(svc, env):  # type: ignore[no-untyped-def]
    from othor.engine.context import EngineContext

    return EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)


def test_home_card_keeps_the_days_issue_when_a_rerun_made_none(night):  # noqa: F811
    """21 Nov 2026 (indigodemof): a rerun with no issue of its own for a brief (its issues removed,
    or the brief failed in it) left Home's card on "No issue yet" while that day's 06:00 issue was
    there. The card falls back to the latest issue of the same day."""
    env, h, cid, rid, svc = night
    env.clock.t = AT_1540
    assert _press(env, h, "/home").status_code == 303
    _work(env, svc)
    with svc.sf() as s, tenant_scope(cid):
        rerun = s.scalars(sa.select(Run).where(Run.kind == "rerun")).one()
        nightly = s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id == rid)).all()
        s.execute(sa.delete(BriefIssue).where(BriefIssue.run_id == rerun.id))
        s.commit()
        titles = [i.title for i in nightly if i.title]
    assert titles
    page = env.client.get(h + "/home").text
    assert "No issue yet" not in page
    assert any(t[:40] in page for t in titles)
    assert "sent 6:00 AM" in page or "goes out at 6:00 AM" in page  # the 06:00 line, not a schedule
