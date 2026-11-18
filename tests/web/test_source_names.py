# SPDX-License-Identifier: Apache-2.0
"""A source's plain name (0.26, M26.3) on the sample night: Data sources › Manage suggests one,
built by code from what Othor AI wrote about its tables at the scan; Use saves it in place with
an event; the card and the chat's source tags show it first; empty goes back to the connector's
name."""

from __future__ import annotations

import sqlalchemy as sa

from othor.connectors import naming
from othor.connectors.models import Source
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_in_place import _post
from tests.web.test_sweep_web import night  # noqa: F401


def _src(svc, cid, name="sales_dw") -> Source:
    with svc.sf() as s, tenant_scope(cid):
        src = s.scalars(sa.select(Source).where(Source.name == name)).one()
        s.expunge(src)
        return src


def test_a_suggested_plain_name_is_saved_on_use_and_shown_first(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    src = _src(svc, cid)
    with svc.sf() as s, tenant_scope(cid):
        got = naming.suggest(s, s.get(Source, src.id))
    assert got and got != "sales_dw" and len(got) <= naming.MAX
    page = env.client.get(f"{h}/sources?panel=manage&s={src.id}").text
    assert "Plain name" in page and f"Othor AI suggests “{got}”" in page
    with svc.sf() as s, tenant_scope(cid):
        assert s.get(Source, src.id).display_name is None  # nothing renamed until the owner does
    r = _post(env, h, f"/sources/{src.id}/name", {"display_name": "", "use": "1"})
    assert r.status_code == 200 and "Plain name" in r.text  # back on Manage, in place
    assert _src(svc, cid).display_name == got
    with svc.sf() as s, tenant_scope(cid):
        ev = s.scalars(sa.select(Event).where(Event.action == "source.renamed")).one()
        assert ev.after == {"display_name": got} and ev.before == {"display_name": None}
    assert f"{got} · " in env.client.get(f"{h}/sources").text
    # their own words, cleaned; empty shows the connector's name again
    _post(env, h, f"/sources/{src.id}/name", {"display_name": "  Sales\n records  "})
    assert _src(svc, cid).display_name == "Sales records"
    _post(env, h, f"/sources/{src.id}/name", {"display_name": ""})
    assert _src(svc, cid).display_name is None
