# SPDX-License-Identifier: Apache-2.0
"""M15.7 · the company's logo: uploaded in Settings (owner, PNG or JPEG, re-encoded), served by its
hash on the company's host, shown on the sign-in page, the link page and the emailed brief
(not the side menu: v0.21); and the brief as a PDF from a recipient's link."""

from __future__ import annotations

import datetime as dt
import io
import re

import sqlalchemy as sa
from PIL import Image

from othor.core.models import Company, Event
from othor.core.tenancy import system_scope, tenant_scope
from othor.mail import delivery
from tests.unit.mail.world import build
from tests.web.conftest import Env, signup

DAY = dt.date(2026, 11, 1)
SIX = dt.datetime(2026, 11, 1, 0, 30, tzinfo=dt.UTC)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (240, 80), (200, 40, 40)).save(buf, format="PNG")
    return buf.getvalue()


def _upload(env: Env, h: str, data: bytes, name: str = "logo.png"):
    tok = env.csrf(h + "/settings")
    return env.client.post(
        h + "/settings/logo",
        files={"file": (name, data, "image/png")},
        headers={"X-CSRF-Token": tok, "HX-Request": "true"},
    )


def _cid(env: Env):
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, system_scope("test"):
        return s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one(), svc


def test_the_owner_sets_and_removes_the_logo(env: Env):
    env.clock.t = SIX
    signup(env)
    h = env.host("indigothread")
    page = env.client.get(h + "/settings").text
    assert "Logo" in page and "Upload" in page and "co-logo" not in page  # none yet
    r = _upload(env, h, _png())
    assert r.status_code == 200 and "Logo saved" in r.text and "Replace" in r.text
    sha = re.search(r'src="/logo/([0-9a-f]{64})\.png"', r.text).group(1)
    img = env.client.get(h + f"/logo/{sha}.png")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.content.startswith(b"\x89PNG")
    assert env.client.get(h + f"/logo/{'0' * 64}.png").status_code == 404
    # not in the side menu (parked to v0.21, owner 13 Nov 2026)
    assert f"/logo/{sha}.png" not in env.client.get(h + "/home").text
    # refused: an SVG, and a GIF named .png
    bad = _upload(env, h, b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "logo.svg")
    assert bad.status_code == 400 and "A PNG or JPEG image, please." in bad.text
    assert _upload(env, h, b"GIF89a" + b"\0" * 30).status_code == 400
    cid, svc = _cid(env)
    with svc.sf() as s, tenant_scope(cid):
        acts = [e.action for e in s.scalars(sa.select(Event)).all()]
    assert "company.logo_set" in acts
    # remove
    tok = env.csrf(h + "/settings")
    r = env.client.post(h + "/settings/logo/remove", headers={"X-CSRF-Token": tok})
    assert "Logo removed" in r.text and "Upload" in r.text
    assert env.client.get(h + f"/logo/{sha}.png").status_code == 404
    assert "co-logo" not in env.client.get(h + "/home").text


def test_the_sign_in_page_link_page_email_and_link_pdf_carry_it(env: Env):
    env.clock.t = SIX - dt.timedelta(hours=1)
    signup(env)
    h = env.host("indigothread")
    r = _upload(env, h, _png())
    sha = re.search(r'src="/logo/([0-9a-f]{64})\.png"', r.text).group(1)
    cid, svc = _cid(env)
    w = build(svc.sf, cid)
    w.recipient("priya@indigothread.example", "Priya Menon", "Head of Sales")
    w.night(DAY)
    env.clock.t = SIX
    with svc.sf() as s, tenant_scope(cid):
        out = delivery.deliver_brief(
            s, svc.settings, svc.key, svc.relay, w.company(s), w.brief_id, DAY, SIX
        )
    assert out.sent == 2
    m = next(m for m in env.relay.outbox if m.to == "priya@indigothread.example")
    assert "cid:company-logo@othor.ai" in m.html
    assert any(i.cid == "company-logo@othor.ai" for i in m.images)
    env.client.cookies.clear()  # a recipient, and the sign-in page, have no session
    assert f"/logo/{sha}.png" in env.client.get(h + "/login").text
    o = re.search(r"https://indigothread\.othor\.localhost(/l/[\w-]+)", m.text).group(1)
    page = env.client.get(h + o).text
    assert f"/logo/{sha}.png" in page and "Download PDF" in page
    pdf = env.client.get(h + o + "/pdf?charts=1&appendix=1")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    with svc.sf() as s, tenant_scope(cid):
        ev = s.scalars(sa.select(Event).where(Event.action == "brief.pdf")).one()
    assert ev.actor != "" and ev.after == {"charts": True, "appendix": True}
    # an expired link can't download it
    env.clock.t = SIX + dt.timedelta(days=8)
    assert env.client.get(h + o + "/pdf").status_code == 404
