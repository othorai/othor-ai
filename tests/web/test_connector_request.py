# SPDX-License-Identifier: Apache-2.0
"""M15.4 · Request a connector (owner, 13 Nov 2026): from Add a source, in the app only; four
fields, only the system required; stored per company with an event, emailed to support and
counted in `othor admin requests`; no stopgap line."""

from __future__ import annotations

import sqlalchemy as sa

from othor.connectors.models import ConnectorRequest
from othor.core.models import Event
from othor.core.tenancy import system_scope, tenant_scope
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _post(env, h, data, next_="/sources"):
    tok = env.csrf(h + "/sources?panel=request")
    return env.client.post(
        h + "/sources/request",
        data={"csrf": tok, "next": next_, **data},
        follow_redirects=False,
    )


def test_add_a_source_offers_the_request_form(night):  # noqa: F811
    env, h, _cid, _rid, _svc = night
    add = env.client.get(h + "/sources?panel=add").text
    assert "Not listed?" in add and 'href="/sources?panel=request"' in add
    form = env.client.get(h + "/sources?panel=request").text
    for text in (
        "Request a connector",
        'name="system"',
        'name="version"',
        'name="wants"',
        'name="reach"',
        "A web login",
        "Export files (Excel, CSV)",
        "We build the most requested first",
    ):
        assert text in form, text
    assert "upload" not in form.lower()  # no stopgap line until file upload ships (Q2.1)


def test_a_request_is_stored_emailed_and_counted(night):  # noqa: F811
    from othor.admin import report

    env, h, cid, _rid, svc = night
    env.relay.outbox.clear()
    r = _post(
        env, h, {"system": "Zoho Books", "version": "2024", "wants": "Invoices", "reach": "web"}
    )
    assert r.status_code == 303 and r.headers["location"].startswith(
        "/sources?panel=add&requested="
    )
    with svc.sf() as s, tenant_scope(cid):
        row = s.scalars(sa.select(ConnectorRequest)).one()
        assert (row.system, row.version, row.wants, row.reach) == (
            "Zoho Books",
            "2024",
            "Invoices",
            "web",
        )
        assert s.scalars(sa.select(Event).where(Event.action == "connector.requested")).one()
    (msg,) = [m for m in env.relay.outbox if m.to == svc.settings.access_request_to]
    assert "Zoho Books" in msg.subject and "Invoices" in msg.text and "A web login" in msg.text
    assert msg.reply_to  # the owner, so support can answer
    done = env.client.get(h + r.headers["location"]).text
    assert "<b>Request sent · Zoho Books</b>" in done and "we’ll email you when it’s ready" in done
    # the name is the owner's text: escaped, never markup
    r2 = _post(env, h, {"system": "<i>x</i>"})
    assert "<i>x</i>" not in env.client.get(h + r2.headers["location"]).text
    _post(env, h, {"system": "zoho books "})
    with svc.sf() as s, tenant_scope(cid):
        s.execute(sa.delete(ConnectorRequest).where(ConnectorRequest.system == "<i>x</i>"))
        s.commit()
    with svc.sf() as s, system_scope("test"):
        rows = report.connector_requests(s)
    assert rows[0]["system"] == "Zoho Books" and rows[0]["requests"] == 2


def test_the_system_is_required(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    r = _post(env, h, {"system": "  ", "wants": "x"})
    assert r.status_code == 303 and "panel=request" in r.headers["location"]
    assert "Name the system you use" in env.client.get(h + r.headers["location"]).text
    r = _post(env, h, {"system": "Tally", "reach": "telepathy"}, next_="https://evil.example")
    assert r.status_code == 303 and r.headers["location"].startswith("/sources?")
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalars(sa.select(ConnectorRequest)).one().reach is None


def test_onboarding_offers_it_too(env):
    """Step 4 lists the same connectors; Not listed? opens the same form over it."""
    from othor.core.models import Company, SetupState
    from tests.web.conftest import signup

    signup(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, system_scope("test"):
        cid = s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one()
    with svc.sf() as s, tenant_scope(cid):
        s.scalars(sa.select(SetupState)).one().current_step = "sources"
        s.commit()
    h = env.host("indigothread")
    page = env.client.get(h + "/setup/sources").text
    assert 'href="/setup/sources?panel=request"' in page
    form = env.client.get(h + "/setup/sources?panel=request").text
    assert "Request a connector" in form and 'name="next" value="/setup/sources"' in form
    tok = env.csrf(h + "/setup/sources?panel=request")
    r = env.client.post(
        h + "/sources/request",
        data={"csrf": tok, "next": "/setup/sources", "system": "Tally Prime"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/setup/sources?requested=Tally%20Prime"
    assert "Request sent · Tally Prime" in env.client.get(h + r.headers["location"]).text
