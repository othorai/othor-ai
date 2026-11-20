# SPDX-License-Identifier: Apache-2.0
"""M15.7c in a browser: choosing Weekly in Edit brief · Delivery posts the form in place and the
"On" picker appears, with no reload. Also guards the in-place handler against a form whose
fields include one named "action" (Edit brief's buttons), which shadows `form.action`."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from othor.briefs.models import Brief
from othor.core.tenancy import tenant_scope
from tests.ui.test_capture_states import serve, session_cookie
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

pytestmark = pytest.mark.browser


def test_weekly_brings_up_on_in_place(night):  # noqa: F811
    from playwright.sync_api import sync_playwright

    env, _h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        bid = s.scalars(sa.select(Brief.id)).one()
    name, val = session_cookie(env).split("=", 1)
    with serve(env.app) as port, sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 1000})
        ctx.add_cookies(
            [{"name": name, "value": val, "domain": f"indigothread.{BASE}", "path": "/"}]
        )
        pg = ctx.new_page()
        posts: list[tuple[int, str]] = []
        pg.on(
            "response",
            lambda r: posts.append((r.status, r.url)) if r.request.method == "POST" else None,
        )
        pg.goto(f"http://indigothread.{BASE}:{port}/brief/{bid}/edit?tab=delivery")
        pg.evaluate("window.__still = true")  # gone after a reload
        assert pg.locator("select[name=on]").count() == 0
        pg.select_option("#f-schedule", "weekly")
        pg.wait_for_selector("select[name=on]", timeout=5000)
        assert pg.input_value("select[name=on]") == "mon"
        assert pg.evaluate("window.__still === true")
        assert posts and all(st == 200 and url.endswith(f"/brief/{bid}/edit") for st, url in posts)
        b.close()


def test_the_created_toast_goes_and_leaves_the_address(night):  # noqa: F811
    """#96: "<name> created" left the screen only by Undo, and "?created=" in the address brought
    it back on Back or a reload. Now the address drops it at once and the toast goes on its own
    (12 s with Undo), held while the pointer is on it; the new brief opens (no 500)."""
    from playwright.sync_api import sync_playwright

    from othor.briefs import service
    from othor.briefs.models import Brief as B

    env, _h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        base = s.scalars(sa.select(B)).one()
        body = {**service.current(s, base).body, "name": "Customer retention"}
        new = service.create(s, body, actor="test")
        s.commit()
        nid = new.id
    name, val = session_cookie(env).split("=", 1)
    with serve(env.app) as port, sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 1000})
        ctx.add_cookies(
            [{"name": name, "value": val, "domain": f"indigothread.{BASE}", "path": "/"}]
        )
        pg = ctx.new_page()
        pg.clock.install()
        pg.goto(f"http://indigothread.{BASE}:{port}/brief?created={nid}")
        toast = pg.locator(".toast-dock .toast")
        assert toast.count() == 1 and "Customer retention created" in toast.inner_text()
        assert "created=" not in pg.url
        toast.hover()
        pg.clock.run_for(20_000)
        assert toast.count() == 1  # held while the pointer is on it
        pg.mouse.move(5, 5)
        pg.clock.run_for(13_000)
        assert toast.count() == 0
        pg.reload()
        assert pg.locator(".toast-dock .toast").count() == 0
        r = pg.goto(f"http://indigothread.{BASE}:{port}/brief/{nid}")
        assert r is not None and r.status == 200
        assert "No issue yet" in pg.inner_text("main")
        b.close()
