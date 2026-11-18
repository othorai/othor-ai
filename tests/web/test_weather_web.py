# SPDX-License-Identifier: Apache-2.0
"""Business weather on Home (v0.12 part 1a) on the steady 1 Nov night: the banner reads the run's
stored weather (computed at its brief step), above the briefs, with the phone scene, alt
text, the line as real text and "How this was picked". The first brief is Sunrise; the next night
picks its scene from the data, and replay reproduces it byte for byte. Needs `make dev && make
seed`."""

from __future__ import annotations

import datetime as dt
import json

import sqlalchemy as sa

from othor.briefs import weather
from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.context import EngineContext
from othor.engine.models import Run
from othor.engine.replay import replay
from othor.engine.store import RunFiles
from tests.fixtures.sample_company.history import STEADY_RUN
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _next_night(env, svc, cid):  # type: ignore[no-untyped-def]
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    env.clock.t = dt.datetime(2026, 11, 1, 21, 0, tzinfo=dt.UTC)  # 2:30 AM IST, Mon 2 Nov
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s,
            c,
            kind="nightly",
            run_date=STEADY_RUN + dt.timedelta(days=1),
            now=ctx.clock(),
            image_version="0.1.0",
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) in ("done", "partial")
    return ctx, rid


def _stored(svc, cid, rid) -> dict:  # type: ignore[no-untyped-def]
    path = RunFiles(svc.settings.runs_dir(cid), rid).outputs / weather.WEATHER
    return json.loads(path.read_bytes())


def test_home_shows_the_first_briefs_sunrise(night):  # noqa: F811
    env, h, cid, rid, svc = night
    w = _stored(svc, cid, rid)
    assert w["scene"] == "sunrise" and w["file"] == "13-sunrise"
    page = env.client.get(h + "/home").text
    assert 'class="weather wx-sunrise"' in page
    # above the briefs
    assert page.index('class="weather ') < page.index('aria-label="Briefs"')
    assert "Today · First brief" in page and w["line"] in page
    # no picture, only the scene's pastel (owner, 17 Nov 2026)
    assert "weather-scene" not in page and "/static/weather/web/" not in page
    # the icon inline, hidden from screen readers (the kicker says the scene)
    assert '<svg aria-hidden="true" focusable="false"' in page
    with svc.sf() as s, tenant_scope(cid):
        n = s.scalars(sa.select(Run.number).where(Run.id == rid)).one()
    # part 1b: "How this was picked" is hidden on the banner for now (owner, 12 Nov 2026); its
    # panel still opens at its address, the run as a link
    assert "How this was picked</a>" not in page
    picked = env.client.get(h + "/home?panel=weather").text
    assert 'class="panel wide"' in picked and w["rule"] in picked
    assert f"#{n:04d} · " in picked and f"/sweep?run={n}" in picked


def test_the_next_night_picks_its_scene_and_replays(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    ctx, rid = _next_night(env, svc, cid)
    w = _stored(svc, cid, rid)
    assert w["scene"] != "sunrise" and w["scene"] in weather.SCENES
    page = env.client.get(h + "/home").text
    assert f'class="weather wx-{w["scene"]}"' in page
    assert f"Today · {w['label']}" in page
    with tenant_scope(cid):
        assert replay(ctx, rid).ok
    # a changed weather.json is caught by replay
    path = RunFiles(svc.settings.runs_dir(cid), rid).outputs / weather.WEATHER
    path.write_text(path.read_text().replace(w["line"], "Something else."))
    with tenant_scope(cid):
        r = replay(ctx, rid)
    assert not r.ok and any(d.startswith("weather") for d in r.diff)


def test_icon_mask_ids_are_made_unique() -> None:
    """An icon with a mask (Fog's eye with a slash in the business set, 15 Nov 2026; No signal's
    before) gets a unique id each time it's on a page."""
    from othor.web import weather_view

    a = str(weather_view.icon("04-fog", "a"))
    b = str(weather_view.icon("04-fog", "b"))
    assert 'id="icon-04-cut-a"' in a and "url(#icon-04-cut-a)" in a
    assert 'id="icon-04-cut-b"' in b and 'id="icon-04-cut"' not in a + b


def test_home_lists_each_source_with_its_update_status(night):  # noqa: F811
    """#57 (owner, 14 Nov 2026): Home's Data sources table reads the run's stored facts; every
    source the spec reads is named by the same name as its Source row."""
    from othor.connectors.models import Source
    from othor.web.home_view import source_status

    env, h, cid, rid, svc = night
    w = _stored(svc, cid, rid)
    fresh = w["facts"]["sources_fresh"]
    with svc.sf() as s, tenant_scope(cid):
        names = {
            src.name
            for src in s.scalars(sa.select(Source).where(Source.status == "connected")).all()
        }
    assert fresh and set(fresh) <= names
    page = env.client.get(h + "/home").text
    assert '<section class="home-sources" aria-label="Data sources">' in page
    assert f"Data sources · {len(names)}" in page and 'href="/sources">Data sources →' in page
    for n in names:
        status, dot, _newest = source_status(n, w["facts"])
        assert f'<span class="dot {dot}"></span>{status}' in page
    # below the Sweep box, above the questions
    assert page.index('aria-label="Sweep"') < page.index('class="home-sources"')
