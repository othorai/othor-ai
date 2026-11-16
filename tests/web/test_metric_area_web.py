# SPDX-License-Identifier: Apache-2.0
"""The metric panel's Area (M16.1): a select of the short list, the owner's own names and Other
numbers, saved on change in place (a new Ontology version and its event, never a new series);
"Add an area" takes the owner's own short name; a name with a number in it is refused."""

from __future__ import annotations

import sqlalchemy as sa

from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.ontology import service
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_ontology_web import _post
from tests.web.test_sweep_web import night  # noqa: F401


def _area(svc, cid, key):
    with svc.sf() as s, tenant_scope(cid):
        rec = next(r for r in service.active(s, ["metric"]) if r.key == key)
        return rec.body.get("area"), service.current_number(s)


def test_the_panel_shows_the_area_select(night):  # noqa: F811
    env, h, *_ = night
    p = env.client.get(h + "/ontology?metric=dso").text
    assert ">Area<" in p and 'action="/ontology/metric/dso/area"' in p
    assert 'data-autosubmit aria-label="Area"' in p
    for a in ("Sales", "Collections", "Operations", "Finance", "Customers", "Other numbers"):
        assert f">{a}</option>" in p, a
    assert '<option value="Collections" selected>' in p
    assert "Add an area" in p and "Where Summary shows this number" in p


def test_changing_the_area_writes_a_version_and_keeps_the_series(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    _, n = _area(svc, cid, "dso")
    r = _post(env, h, "/ontology/metric/dso/area", {"area": "Finance"})
    assert r.status_code == 303 and r.headers["location"] == "/ontology?metric=dso"
    assert _area(svc, cid, "dso") == ("Finance", n + 1)
    with svc.sf() as s, tenant_scope(cid):
        ev = s.scalars(sa.select(Event).where(Event.action == "ontology.version_created")).all()
        assert ev
    # Other numbers takes the area off
    _post(env, h, "/ontology/metric/dso/area", {"area": ""})
    assert _area(svc, cid, "dso") == (None, n + 2)
    # the same choice again changes nothing
    _post(env, h, "/ontology/metric/dso/area", {"area": ""})
    assert _area(svc, cid, "dso") == (None, n + 2)


def test_the_owner_adds_their_own_area(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    _post(env, h, "/ontology/metric/export_share/area", {"area": "  Export desk "})
    assert _area(svc, cid, "export_share")[0] == "Export desk"
    p = env.client.get(h + "/ontology?metric=dso").text
    assert ">Export desk</option>" in p  # now in every number's select


def test_a_bad_name_is_refused_and_an_unknown_number_is_404(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    before = _area(svc, cid, "dso")
    r = _post(env, h, "/ontology/metric/dso/area", {"area": "Top 10"})
    assert r.status_code == 400 and r.headers.get("X-Othor-Toast") == "1"
    assert _area(svc, cid, "dso") == before
    assert _post(env, h, "/ontology/metric/nope/area", {"area": "Sales"}).status_code == 404
