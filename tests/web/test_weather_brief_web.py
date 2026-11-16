# SPDX-License-Identifier: Apache-2.0
"""Each brief's own weather (v0.12 part 1c) on the 2 Nov night, with a second brief scoped to
dispatch times: the run stores each brief's scene, line and outlook next to the company's
(`weather_briefs.json`; `weather.json` unchanged); the unscoped Leadership brief has the
company's weather byte for byte, the Dispatch brief its own. The brief page shows its banner and
outlook at its head; the forecast panel opens in place and closes to the brief; the recipients'
view shows both with no panel links. Replay reproduces the file byte for byte. Needs `make dev &&
make seed`."""

from __future__ import annotations

import html
import json
import re
from typing import Any

import sqlalchemy as sa

from othor.briefs import service, weather
from othor.briefs.models import Brief
from othor.core.tenancy import tenant_scope
from othor.engine.replay import replay
from othor.engine.store import RunFiles
from othor.ontology.compiler.api import canonical
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401
from tests.web.test_weather_web import _next_night, _stored

DISPATCH = {
    "name": "Dispatch",
    "question": "Are dispatches on time, and where do I need to step in?",
    "scope": {"metrics": ["dispatch_time"], "exclude": {}},
    "headline": ["dispatch_time"],
    "ask_owner": "top_item",
    "goals": [],
    "schedule": "daily",
    "time": "06:00",
    "email_format": "simple",
}


def two_briefs(env, svc, cid):  # type: ignore[no-untyped-def]
    """Leadership (whole company) and Dispatch (dispatch times only), then the 2 Nov night."""
    with svc.sf() as s, tenant_scope(cid):
        lead = s.scalars(sa.select(Brief.id)).one()
        disp = service.create(s, DISPATCH, actor="test", reason="test").id
        s.commit()
    ctx, rid = _next_night(env, svc, cid)
    return ctx, rid, lead, disp


def _per_brief(svc, cid, rid) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    path = RunFiles(svc.settings.runs_dir(cid), rid).outputs / weather.BRIEFS
    return json.loads(path.read_bytes())["briefs"]


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def test_each_brief_has_its_own_weather_at_its_head(night, monkeypatch):  # noqa: F811
    monkeypatch.setattr(weather, "IN_BRIEFS", True)  # hidden by default since #62; kept for v0.21
    env, h, cid, _rid, svc = night
    ctx, rid, lead, disp = two_briefs(env, svc, cid)
    company = _stored(svc, cid, rid)
    mine = _per_brief(svc, cid, rid)
    # the unscoped brief is the company's weather, byte for byte; the scoped one its own
    assert canonical(mine[str(lead)]["weather"]) == canonical(company)
    wl, wd = mine[str(lead)]["weather"], mine[str(disp)]["weather"]
    assert mine[str(disp)]["scope"]["metrics"] == ["dispatch_time"]
    assert wd["scene"] != wl["scene"], (wd["scene"], wl["scene"])

    for bid, w in ((lead, wl), (disp, wd)):
        page = env.client.get(h + f"/brief/{bid}").text
        text = _text(page)
        # the banner at the head of the brief, above the question and the title
        assert f'class="weather wx-{w["scene"]}"' in page
        assert page.index('class="weather ') < page.index('class="brief-kicker')
        assert page.index('class="brief-kicker') < page.index('class="brief-title')
        assert f"Today · {w['label']}" in text and w["line"] in text
        assert f'alt="{weather.alt(w["file"])}"' in html.unescape(page)
        # the phone scene, as on Home
        assert f'media="(max-width: 639px)" srcset="/static/weather/phone/{w["file"]}.webp"' in page
        # the outlook under it, each card opening the brief's forecast panel in place
        o = w["outlook"]
        assert page.count('class="wx-week wk-') == 4
        for k in o["weeks"]:
            assert k["label"] in text  # #58: no line on the cards
            assert (
                f'href="/brief/{bid}?date=2026-11-02&amp;panel=forecast&amp;week={k["week"]}"'
                in page
            )
        assert "See the forecast →" not in page  # #58: no Ahead line under the cards
        assert ' style="' not in page
        assert "How this was picked" not in page  # hidden for now, as on Home

    # the forecast panel on the brief, in place; its close returns to the brief
    o = wl["outlook"]
    assert o["ahead"]
    url = f"/brief/{lead}?date=2026-11-02&panel=forecast&week=ahead"  # still at its address
    p = env.client.get(h + url, headers={"X-Othor-Panel": "1"})
    assert p.status_code == 200
    panel = p.text[p.text.index('id="overlay"') :]
    assert 'class="panel wide"' in panel and "What’s driving it" in panel
    assert o["ahead_line"] in html.unescape(panel) and "<svg" in panel
    assert f'class="scrim" href="/brief/{lead}?date=2026-11-02"' in panel
    assert ' style="' not in p.text
    for k in o["weeks"]:
        q = env.client.get(h + f"/brief/{lead}?date=2026-11-02&panel=forecast&week={k['week']}")
        assert 'class="panel wide"' in q.text and k["name"] in html.unescape(q.text)
    # the Dispatch brief's panel reads its own outlook
    q = env.client.get(h + f"/brief/{disp}?panel=forecast&week=1").text
    assert 'class="panel wide"' in q and wd["outlook"]["weeks"][0]["line"] in html.unescape(q)

    # the recipients' view: the banner and the outlook, no panel links
    shared = env.client.get(h + f"/brief/{disp}/shared").text
    assert f'class="weather wx-{wd["scene"]}"' in shared and wd["line"] in _text(shared)
    assert shared.count('class="wx-week wk-') == 4 and "is-static" in shared
    assert "panel=forecast" not in shared and "See the forecast" not in shared
    assert ' style="' not in shared

    # replay: each brief's weather byte for byte; a change is caught
    with tenant_scope(cid):
        assert replay(ctx, rid).ok
    path = RunFiles(svc.settings.runs_dir(cid), rid).outputs / weather.BRIEFS
    doc = json.loads(path.read_bytes())
    doc["briefs"][str(disp)]["weather"]["line"] = "Something else."
    path.write_bytes(canonical(doc).encode())
    with tenant_scope(cid):
        r = replay(ctx, rid)
    assert not r.ok and any(d.startswith("brief weather") for d in r.diff)


def test_a_run_sealed_before_part_1c_shows_no_banner_on_the_brief(night):  # noqa: F811
    env, h, cid, rid, svc = night
    (RunFiles(svc.settings.runs_dir(cid), rid).outputs / weather.BRIEFS).unlink()
    with svc.sf() as s, tenant_scope(cid):
        bid = s.scalars(sa.select(Brief.id)).one()
    page = env.client.get(h + f"/brief/{bid}").text
    assert 'class="weather ' not in page and 'class="brief-title' in page


def test_the_weather_is_hidden_in_briefs_and_kept_on_home(night):  # noqa: F811
    """#62 (owner, 14 Nov 2026): no weather banner or outlook in a brief (page, shared link,
    email) until v0.21 revisits it; Home keeps it."""
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        bid = s.scalars(sa.select(Brief.id)).first()
    page = env.client.get(h + f"/brief/{bid}").text
    assert 'class="wx-' not in page
    home = env.client.get(h + "/home").text
    assert 'class="wx-' in home
