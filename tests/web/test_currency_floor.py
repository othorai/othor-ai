# SPDX-License-Identifier: Apache-2.0
"""M15.6 · the currency and the floor in Settings: a change reaches the next night's spec through
an Ontology version (versioned, Undo, replay exact); the floor is the owner's; US zones."""

from __future__ import annotations

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.ontology import service
from othor.ontology.compiler import compile_version
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _set(env, h, field, value):
    tok = env.csrf(h + "/settings")
    return env.client.post(
        h + "/settings", data={"field": field, "value": value}, headers={"X-CSRF-Token": tok}
    )


def _spec(svc, cid):
    from othor.core.models import Company

    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        return dict(compile_version(s, c).compiled), service.current_number(s)


def test_usd_reaches_the_next_nights_spec(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    before, v0 = _spec(svc, cid)
    assert before["company"]["currency"] == "INR"
    assert before["detectors"]["materiality_inr"] == "500000"
    r = _set(env, h, "currency", "USD")
    assert r.status_code == 200 and "Currency saved" in r.text
    after, v1 = _spec(svc, cid)
    assert v1 == v0 + 1  # an Ontology version: the next night compiles with it
    assert after["company"]["currency"] == "USD"
    assert after["detectors"]["materiality_inr"] == "5000"  # $5,000 without the owner's floor
    page = env.client.get(h + "/settings").text
    assert "$" in page and "USD ($), thousands/millions" in page


def test_the_owner_sets_the_floor(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    page = env.client.get(h + "/settings").text
    assert "Tell me about problems worth more than" in page and "₹5 L" in page
    r = _set(env, h, "materiality", "200000")
    assert r.status_code == 200 and "Floor saved" in r.text
    spec, _ = _spec(svc, cid)
    assert spec["detectors"]["materiality_inr"] == "200000"
    assert "₹2 L" in env.client.get(h + "/settings").text
    assert _set(env, h, "materiality", "-3").status_code == 400
    assert _set(env, h, "materiality", "abc").status_code == 400


def test_the_us_zones_are_offered(night):  # noqa: F811
    env, h, _cid, _rid, _svc = night
    page = env.client.get(h + "/settings").text
    for zone in (
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Phoenix",
        "America/Los_Angeles",
        "America/Anchorage",
        "Pacific/Honolulu",
    ):
        assert f'value="{zone}"' in page, zone
    assert _set(env, h, "timezone", "America/New_York").status_code == 200


def test_a_usd_night_writes_dollars_and_replays(night):  # noqa: F811
    """After switching to USD, the next night's brief, Sweep and the Test tab read $ and K/M;
    the run replays identically; an INR page in the same process stays ₹ (no leak)."""
    import datetime as dt
    import re

    from othor.core.models import Company
    from othor.engine import orchestrator
    from othor.engine.context import EngineContext
    from othor.engine.models import Run
    from othor.engine.replay import replay
    from tests.fixtures.sample_company.history import STEADY_RUN

    env, h, cid, _rid, svc = night
    assert _set(env, h, "currency", "USD").status_code == 200
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
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
        assert orchestrator.execute(ctx, rid) == "done"
        assert replay(ctx, rid).ok
    env.clock.t += dt.timedelta(days=1)
    sweep = env.client.get(h + "/sweep").text
    money = re.findall(r'class="finding-money">([^<]+)<', sweep)
    assert money and all(m.startswith(("$", "−$")) for m in money), money
    assert "₹" not in sweep.split('class="finding-row"', 1)[1]
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
    brief = env.client.get(h + "/brief").text
    assert "$" in brief
    fp = re.search(r"/sweep/finding/([0-9a-f]+)\?run=" + str(run.number), sweep).group(1)
    test = env.client.get(h + f"/sweep/finding/{fp}?run={run.number}&tab=test").text
    i = test.find("floor")
    assert "your floor is $5K" in test, test[i - 200 : i + 200] if i >= 0 else test[-1500:]
