# SPDX-License-Identifier: Apache-2.0
"""0.28.5 previews for the owner: "Since last week" as computed on a real night. Dev only, run by
hand (needs `make dev`):

    OTHOR_EVENT_NIGHTS=8 .venv/bin/python -m pytest tests/ui/preview_since_week.py \
        -s -p no:cacheprovider -o addopts=""

Runs the sample's events nights 20–27 Nov (Ravira invoices again on 24 Nov), takes the
Leadership issue of 26 Nov as the run made it, puts it in the signed-in 1 Nov preview world, and
writes the page (desktop and phone), the PDF and the email (HTML and a screenshot) to
~/Desktop/othor-preview/since-last-week/."""

from __future__ import annotations

import copy
import datetime as dt
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, cast

os.environ.setdefault("OTHOR_EVENT_NIGHTS", "8")

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from tests.e2e.test_events import nights  # noqa: F401
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "since-last-week"
DAY = dt.date(2026, 11, 26)


def _issue(ctx, cid, rid) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    from othor.briefs.models import Brief, BriefIssue

    with ctx.sf() as s, tenant_scope(cid):
        brief = s.scalars(sa.select(Brief).where(Brief.name == "Leadership")).one()
        issue = s.scalars(
            sa.select(BriefIssue).where(BriefIssue.brief_id == brief.id, BriefIssue.run_id == rid)
        ).one()
        return {"body": copy.deepcopy(issue.body), "title": issue.title}


def test_preview(nights, night) -> None:  # noqa: F811
    ectx, ecid, runs, _log = nights
    got = _issue(ectx, ecid, runs[DAY])
    assert got["body"].get("week"), "26 Nov's issue has no Since last week"
    env, h, cid, _rid, svc = night
    from othor.briefs.models import Brief, BriefIssue
    from othor.core.models import Company

    with svc.sf() as s, tenant_scope(cid):
        brief = s.scalars(sa.select(Brief).where(Brief.name == "Leadership")).one()
        issue = s.scalars(sa.select(BriefIssue).where(BriefIssue.brief_id == brief.id)).first()
        issue.body, issue.title = got["body"], got["title"]
        s.commit()
        bid = brief.id
    OUT.mkdir(parents=True, exist_ok=True)
    # the PDF and the email, as the app makes them
    pdf = env.client.get(h + f"/brief/{bid}/pdf?charts=1")
    assert pdf.status_code == 200, pdf.status_code
    (OUT / "leadership-26-nov.pdf").write_bytes(pdf.content)
    from othor.mail.delivery import brief_mail
    from othor.mail.messages import brief_email

    with svc.sf() as s, tenant_scope(cid):
        company = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        brief = s.scalars(sa.select(Brief).where(Brief.id == bid)).one()
        issue = s.scalars(sa.select(BriefIssue).where(BriefIssue.brief_id == bid)).first()
        msg = brief_email(
            brief_mail(s, company, brief, issue),
            to="owner@example.com",
            open_url=h + f"/brief/{bid}",
            unsubscribe_url=h + "/u/preview",
            settings=svc.settings,
        )
    html = msg.html
    import base64

    for im in msg.images:  # the inline PNGs (logo, charts) as data URIs, for the screenshot
        html = html.replace(
            f"cid:{im.cid}", f"data:image/png;base64,{base64.b64encode(im.data).decode()}"
        )
    (OUT / "email-26-nov.html").write_text(html)
    (OUT / "email-26-nov.txt").write_text(msg.text)
    import uvicorn

    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = int(so.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(cast(Any, env.app), host="127.0.0.1", port=port, log_level="warning")
    )
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
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
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            pg.goto(f"http://indigothread.{BASE}:{port}/brief/{bid}", wait_until="networkidle")
            pg.screenshot(path=str(OUT / f"brief-26-nov{tag}.png"), full_page=True)
            sec = pg.locator("section.week")
            if sec.count():
                sec.screenshot(path=str(OUT / f"section-26-nov{tag}.png"))
            pg.set_content((OUT / "email-26-nov.html").read_text())
            pg.screenshot(path=str(OUT / f"email-26-nov{tag}.png"), full_page=True)
            ctx.close()
        b.close()
    server.should_exit = True
    th.join(5)
    print("previews in", OUT)
