# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
"""The screenshots in the paper "Analytic Tradecraft as Code" (blog post and marketing PDF), retaken
from the current app (owner, 20 Nov 2026: "please do update the pdf"). Dev only, run by hand
(needs `make dev && make seed`, the 1 Nov night):

    .venv/bin/python -m pytest tests/ui/preview_paper_shots.py -s -p no:cacheprovider -o addopts=""
    .venv/bin/python marketing/paper/build.py

The sample company in US dollars and a US time zone, as the paper shows it; written as webp into
othor_site/static/site/blog/analytic-tradecraft/, the same files the post and the PDF read."""

from __future__ import annotations

import base64
import io
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "othor_site" / "static" / "site" / "blog" / "analytic-tradecraft"


def _port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


def _save(png: bytes, name: str, width: int) -> None:
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGB")
    if im.width != width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(OUT / f"{name}.webp", "WEBP", quality=90, method=6)


def test_paper_shots(night) -> None:  # noqa: F811
    subprocess.run([sys.executable, "scripts/build_css.py"], cwd=ROOT, check=True)
    env, _h, cid, _rid, svc = night
    # US dollars through Settings (the spec is compiled again), a US time zone, then the 1 Nov night
    # run again so the brief's sentences are written in dollars too (as test_brief_pdf's USD night)
    import datetime as dt

    from othor.briefs.models import Brief, BriefIssue
    from othor.core.models import Company
    from othor.core.tenancy import system_scope
    from othor.engine import orchestrator
    from othor.engine.context import EngineContext
    from othor.mail import delivery, messages
    from tests.fixtures.sample_company.history import STEADY_RUN
    from tests.web.test_currency_floor import _set

    with svc.sf() as s, system_scope("preview"):
        co = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        co.timezone = "America/New_York"
        s.commit()
    assert _set(env, _h, "currency", "USD").status_code == 200
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s, c, kind="rerun", run_date=STEADY_RUN, now=ctx.clock(), image_version="0.1.0"
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    del dt
    with svc.sf() as s, tenant_scope(cid):
        co = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        brief = s.scalars(sa.select(Brief).order_by(Brief.created_at)).first()
        assert brief is not None
        bid = brief.id
        issue = s.scalars(
            sa.select(BriefIssue)
            .where(BriefIssue.brief_id == bid)
            .order_by(BriefIssue.date.desc(), BriefIssue.created_at.desc())
        ).first()
        assert issue is not None
        m = delivery.brief_mail(s, co, brief, issue, svc.settings)
        msg = messages.brief_email(
            m, to="owner@example.com", open_url="#", unsubscribe_url="#", settings=svc.settings
        )
    email_html = msg.html
    for img in msg.images:
        email_html = email_html.replace(
            f"cid:{img.cid}", "data:image/png;base64," + base64.b64encode(img.data).decode()
        )

    import uvicorn

    port = _port()
    server = uvicorn.Server(
        uvicorn.Config(cast(Any, env.app), host="127.0.0.1", port=port, log_level="warning")
    )
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
    app = f"http://indigothread.{BASE}:{port}"
    cookies = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain or f"indigothread.{BASE}",
            "path": c.path or "/",
        }
        for c in env.client.cookies.jar
    ]
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        ctx = b.new_context(viewport={"width": 1440, "height": 950}, device_scale_factor=2)
        ctx.add_cookies(cookies)  # type: ignore[arg-type]
        pg = ctx.new_page()

        def shot(
            url: str, name: str, width: int, height: int = 950, wait: str | None = None
        ) -> None:
            pg.set_viewport_size({"width": 1440, "height": height})
            pg.goto(url, wait_until="networkidle")
            if wait:
                pg.wait_for_selector(wait)
            # the 1 Nov night was run again for dollars: leave out its "Send the updated brief?" offer
            pg.evaluate(
                """() => document.querySelectorAll('form[action$="/keep"]').forEach(
                     e => (e.closest('.flex') || e).remove())"""
            )
            pg.wait_for_timeout(400)
            _save(pg.screenshot(), name, width)

        # Figure 1: the Morning Brief at 06:00
        shot(f"{app}/brief/{bid}", "morning-brief", 1600, 950)
        # Figure 5: Sweep, the record of the night (the detectors ring since 20 Nov)
        shot(f"{app}/sweep", "sweep", 1600, 1125)
        # the first finding (net hiding gross) and its Test tab's Method and settings
        href = pg.get_attribute(".finding-title", "href")
        assert href
        pg.set_viewport_size({"width": 1440, "height": 1100})
        pg.goto(f"{app}{href}&tab=test", wait_until="networkidle")
        pg.wait_for_timeout(400)
        head = pg.locator("p.test-head", has_text="Method and settings").first
        head.scroll_into_view_if_needed()
        a = head.bounding_box()
        kv = head.locator("xpath=following-sibling::*[1]").bounding_box()
        assert a and kv
        x0, y0 = a["x"] - 24, a["y"] - 20
        _save(
            pg.screenshot(
                clip={
                    "x": x0,
                    "y": y0,
                    "width": max(a["width"], kv["width"]) + 48,
                    "height": kv["y"] + kv["height"] - y0 + 24,
                }
            ),
            "site-finding",
            1360,
        )
        # Figure 8: "How is this calculated?" beside the brief
        fp = href.split("/sweep/finding/")[1].split("?")[0]
        shot(f"{app}/brief/{bid}/finding/{fp}", "how-it-was-calculated", 1600, 1100)
        # Figure 3: the Business Model
        pg.set_viewport_size({"width": 1120, "height": 739})
        pg.goto(f"{app}/ontology", wait_until="networkidle")
        pg.wait_for_timeout(400)
        _save(pg.screenshot(), "site-ontology", 2240)
        # Figure 10: the brief as an email
        epg = b.new_page(viewport={"width": 640, "height": 900}, device_scale_factor=2)
        epg.set_content(email_html, wait_until="networkidle")
        epg.wait_for_timeout(300)
        full = epg.screenshot(full_page=True)
        from PIL import Image

        im = Image.open(io.BytesIO(full))
        im = im.crop((0, 0, im.width, min(im.height, round(im.width * 1520 / 1280))))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        _save(buf.getvalue(), "site-email", 1280)
        b.close()
    server.should_exit = True
    th.join(5)
    print("shots in", OUT)
