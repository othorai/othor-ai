# SPDX-License-Identifier: Apache-2.0
"""Daily, Weekdays, Weekly or Monthly (M15.7c part B, owner 14 Nov 2026) on the 1 Nov sample
night: the Delivery tab's "How often" and "On", saved as a version; a Weekly brief is made on
its day only, speaks for its week, and its card shows its last issue and the next one between."""

from __future__ import annotations

import datetime as dt
import html
import re

import sqlalchemy as sa

from othor.briefs.models import Brief, BriefIssue, BriefVersion
from othor.core.models import Company, Event
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.context import EngineContext
from othor.engine.replay import replay
from tests.web.test_brief_web import _brief_id, _draft
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _post(env, h, bid, page, **fields):
    data = {
        "csrf": env.csrf(h + f"/brief/{bid}"),
        "draft": html.unescape(re.search(r'name="draft" value="([^"]*)"', page).group(1)),
        "tab": "delivery",
        **fields,
    }
    return env.client.post(h + f"/brief/{bid}/edit", data=data, follow_redirects=False)


def test_how_often_and_on_in_the_delivery_tab_save_a_version(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid = _brief_id(svc, cid)
    tab = env.client.get(h + f"/brief/{bid}/edit?tab=delivery").text
    assert '<select class="select" id="f-schedule" name="schedule" data-autosubmit>' in tab
    for opt in ("Every day", "Weekdays", "Weekly", "Monthly"):
        assert f">{opt}</option>" in tab
    assert 'name="on"' not in tab  # a daily brief has no day to pick
    # choosing Weekly posts the form in place; the tab comes back with "On"
    r = _post(env, h, bid, tab, schedule="weekly", action="preview")
    assert r.status_code == 200 and 'name="on"' in r.text
    for day in ("Monday", "Wednesday", "Sunday"):
        assert f">{day}</option>" in r.text
    assert _draft(r.text)["schedule"] == "weekly" and _draft(r.text)["on"] == "mon"
    m = _post(env, h, bid, r.text, schedule="monthly", action="preview")
    assert ">The 1st</option>" in m.text and ">The 28th</option>" in m.text
    assert ">The 29th</option>" not in m.text
    saved = _post(env, h, bid, r.text, schedule="weekly", on="wed", action="save")
    assert saved.status_code == 303 and saved.headers["location"] == f"/brief/{bid}?saved=2"
    with svc.sf() as s, tenant_scope(cid):
        v2 = s.scalars(sa.select(BriefVersion).where(BriefVersion.number == 2)).one()
        assert (v2.body["schedule"], v2.body["on"]) == ("weekly", "wed")
        assert {"schedule", "on"} <= set(v2.diff)
        assert s.scalars(sa.select(Event).where(Event.action == "brief.changed")).one()


def _night(env, svc, cid, day: dt.date):
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    env.clock.t = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(21, 0), tzinfo=dt.UTC)
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s, c, kind="nightly", run_date=day, now=ctx.clock(), image_version="0.1.0"
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) in ("done", "partial")
    return ctx, rid


def test_a_weekly_brief_is_made_on_its_day_and_speaks_for_its_week(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        brief = s.scalars(sa.select(Brief)).one()
        v = s.scalars(sa.select(BriefVersion)).one()
        v.body = {**v.body, "schedule": "weekly", "on": "tue"}
        s.commit()
        bid = brief.id
    # Monday 2 Nov: no issue of this brief; its card shows Sunday's and when the next comes
    _ctx, mon = _night(env, svc, cid, dt.date(2026, 11, 2))
    with svc.sf() as s, tenant_scope(cid):
        assert not s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id == mon)).all()
    home = env.client.get(h + "/home").text
    assert "Weekly · Tuesdays · next Tue 3 Nov" in home and "No issue yet" not in home
    # Tuesday 3 Nov: the issue, tagged against the last one, "Since last week"
    ctx, tue = _night(env, svc, cid, dt.date(2026, 11, 3))
    with svc.sf() as s, tenant_scope(cid):
        issue = s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id == tue)).one()
        assert issue.body["period"] == {"from": "2026-11-01", "word": "last week"}
    full = env.client.get(h + f"/brief/{bid}?view=full").text
    assert "Since last week’s brief" in full and "<th>Since last week</th>" in full
    assert "Since yesterday" not in full
    with tenant_scope(cid):
        assert replay(ctx, tue).ok  # the period came from the run's own inputs
