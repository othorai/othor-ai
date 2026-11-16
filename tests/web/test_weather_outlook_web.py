# SPDX-License-Identifier: Apache-2.0
"""The outlook on Home (v0.12 part 1b) on the 2 Nov night: four week cards and the Ahead line under
the banner, read from the run's stored weather; the forecast panel opens in place for a week and
for "See the forecast", with its chart as an SVG from render_svg; a Limited-view week opens the
short explanation. The weeks ahead are stored before their actuals and never become a forecast of
record; replay reproduces the weeks-ahead file and the outlook byte for byte. Needs `make dev &&
make seed`."""

from __future__ import annotations

import datetime as dt
import html
import json
import re

import sqlalchemy as sa

from othor.briefs import weather
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.models import Forecast, Run
from othor.engine.replay import replay
from othor.engine.store import RunFiles
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401
from tests.web.test_weather_web import _next_night, _stored


def test_home_shows_the_outlook_and_its_panels(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    ctx, rid = _next_night(env, svc, cid)
    w = _stored(svc, cid, rid)
    o = w["outlook"]
    page = env.client.get(h + "/home").text
    text = html.unescape(page)
    # under the banner, inside the same card, before the checks strip
    assert (
        page.index('class="weather ')
        < page.index('class="wx-outlook"')
        < page.index('class="checks ')
    )
    assert page.count('class="wx-week wk-') == 4
    # #58 (owner, 14 Nov 2026): each card is its name, dates, icon (top right) and label; no line
    # on the cards, no heading above them, no Ahead line under them, no box around them
    week_html = page[page.index('class="wx-outlook"') :].split("</section>", 1)[0]
    for k in o["weeks"]:
        assert f"{k['name']}" in text and k["dates"] in text and k["label"] in text
        assert html.escape(k["line"], quote=False) not in week_html
        assert f'href="/home?panel=forecast&amp;week={k["week"]}"' in page
    if o["worst"]:
        assert re.search(rf"wk-{o['worst']} wx-\w+ is-worst", page)
    assert "Ahead:" not in re.sub(r"<[^>]+>", "", week_html) and "wx-outlook-head" not in page
    assert "See the forecast →" not in page and "wx-week-line" not in page
    assert ' style="' not in page

    # the forecast panel, in place, still at its address and for each week
    if o["ahead"]:
        p = env.client.get(h + "/home?panel=forecast&week=ahead", headers={"X-Othor-Panel": "1"})
        assert p.status_code == 200
        panel = html.unescape(p.text.split('class="panel wide"', 1)[1])
        assert o["ahead_line"] in panel and "What’s driving it" in panel
        assert "How is this calculated?" in panel and "ETS(A," in panel
        assert "<svg" in panel and "Forecast" in panel  # the ChartSpec, rendered by render_svg
        assert ' style="' not in p.text
    for k in o["weeks"]:
        p = env.client.get(h + f"/home?panel=forecast&week={k['week']}").text
        assert 'class="panel wide"' in p and k["name"] in html.unescape(p)
        if k["scene"] == "fog":
            assert "it narrows as more weeks of data come in." in html.unescape(p)

    # the weeks ahead are stored before their actuals, and never a forecast of record
    with svc.sf() as s, tenant_scope(cid):
        rows = s.scalars(sa.select(Forecast).where(Forecast.run_id == rid)).all()
        later = [r for r in rows if (r.params or {}).get("horizon")]
        assert later and all(r.as_of < r.period and r.grain == "week" for r in later)
        assert {r.params["horizon"] for r in later} == {2, 3, 4}
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        nxt = Run(number=run.number + 1)
        record = orchestrator.forecasts_of_record(s, nxt, run.run_date + dt.timedelta(days=30))
    assert record["of_record"]
    ahead_keys = {(r.series, r.period.isoformat(), r.as_of.isoformat()) for r in later}
    assert not ahead_keys & {(r["series"], r["period"], r["as_of"]) for r in record["of_record"]}

    # replay: the weeks-ahead file and the outlook, byte for byte; a change is caught
    with tenant_scope(cid):
        assert replay(ctx, rid).ok
    path = RunFiles(svc.settings.runs_dir(cid), rid).outputs / orchestrator.AHEAD
    doc = json.loads(path.read_bytes())
    doc["rows"][1]["point"] = "1"
    path.write_text(json.dumps(doc))
    with tenant_scope(cid):
        r = replay(ctx, rid)
    assert not r.ok and any(d.startswith("forecasts ahead") for d in r.diff)


def test_how_this_was_picked_links_its_facts(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    _ctx, rid = _next_night(env, svc, cid)
    w = _stored(svc, cid, rid)
    with svc.sf() as s, tenant_scope(cid):
        n = s.scalars(sa.select(Run.number).where(Run.id == rid)).one()
    p = env.client.get(h + "/home?panel=weather").text
    text = html.unescape(p)
    assert "How this was picked" in text and w["rule"] in text and w["line"] in text
    if w["facts"]["items"]:
        assert f'href="/sweep?run={n}&amp;tab=findings"' in p
    near = w["facts"].get("nearest")
    if near:
        assert f'href="/sweep/finding/{near["fingerprint"]}?run={n}"' in p
    assert f"?run={n}" in p and ' style="' not in p
    assert weather.SCENES[w["scene"]].label in text
