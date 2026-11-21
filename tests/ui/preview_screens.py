# SPDX-License-Identifier: Apache-2.0
"""Previews of the main screens for the owner (v0.25 M25.3, typography). Dev only, run by hand:

    OTHOR_PREVIEW_DIR=v0.25/type/before .venv/bin/python -m pytest tests/ui/preview_screens.py \
        -s -p no:cacheprovider -o addopts=""

Needs `make dev && make seed` (the 1 Nov night). Builds the CSS first, then photographs each screen
signed in, desktop (1440) and phone (390), into ~/Desktop/othor-preview/<OTHOR_PREVIEW_DIR>/."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / os.environ.get("OTHOR_PREVIEW_DIR", "screens")
ROOT = Path(__file__).resolve().parents[2]


def _port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


def test_preview(night) -> None:  # noqa: F811
    import sys

    subprocess.run([sys.executable, "scripts/build_css.py"], cwd=ROOT, check=True)
    env, _h, cid, _rid, svc = night
    svc.settings.public_site = True  # the website on the bare host, as othor_site's tests do
    shot = os.environ.get("OTHOR_PREVIEW_SHOT")  # a What's new cover's shot: one page, in USD
    if shot:
        from othor.core.models import Company
        from othor.core.tenancy import system_scope

        with svc.sf() as s, system_scope("preview"):
            co = s.scalars(sa.select(Company).where(Company.id == cid)).one()
            co.currency, co.timezone = "USD", "America/New_York"
            s.commit()
    OUT.mkdir(parents=True, exist_ok=True)
    from othor.briefs.models import Brief

    with svc.sf() as s, tenant_scope(cid):
        brief = s.scalars(sa.select(Brief.id).order_by(Brief.created_at)).first()
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
    site = f"http://{BASE}:{port}"
    cookies = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain or f"indigothread.{BASE}",
            "path": c.path or "/",
        }
        for c in env.client.cookies.jar
    ]
    shots = [
        ("home", app + "/home"),
        ("briefs", app + "/brief"),
        ("brief", app + f"/brief/{brief}"),
        ("sweep", app + "/sweep"),
        ("summary", app + "/summary"),
        ("business-model", app + "/ontology"),
        ("sources", app + "/sources"),
        ("settings", app + "/settings"),
        ("site-home", site + "/"),
        ("site-pricing", site + "/pricing"),
        ("site-whats-new", site + "/whats-new"),
        ("site-security", site + "/security"),
        ("sign-in", site + "/login"),
    ]
    only = [x for x in os.environ.get("OTHOR_PREVIEW_ONLY", "").split(",") if x]
    if only:  # OTHOR_PREVIEW_ONLY=summary,summary-open,sweep-history,ask: just these, in INR
        extra = {
            "summary-open": app + "/summary",
            "sweep-history": app + "/sweep/history",
            "ask": app + "/home?panel=othor&subject=ask",
            "goal": app + "/ontology?panel=othor&subject=ontology:goal",
            "new-brief": app + "/brief/new",
            "new-brief-review": app + "/brief/new",
            "sweep-ring": app + "/sweep",
            "usage": app + "/usage",
            "archive": app + "/brief/archive",
            # Summary 1.1: every number's line (the week Receivables over 60 days shifted,
            # marked), and a look-alike pair's merge draft open in the panel
            "summary-numbers": app + "/summary?rows=numbers",
            "summary-merge": app
            + f"/summary?panel=othor&subject=proposal:{_merge_draft(svc, cid)}",
        }
        shots = [(n, u) for n, u in shots if n in only] + [
            (n, extra[n]) for n in only if n in extra
        ]
        if "ask" in only:
            _ask_sample(env, svc, cid)
    if shot:  # OTHOR_PREVIEW_SHOT=<page path>:<file>, e.g. /brief/{brief}:smooth-lines.png
        path, fname = shot.split(":", 1)
        shots = [(fname.removesuffix(".png"), app + path.replace("{brief}", str(brief)))]
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1280, ""),) if shot else ((1440, ""), (390, "-phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            for name, url in shots:
                pg.goto(url, wait_until="networkidle")
                if name == "new-brief-review":  # #95: step 2, from the first suggestion
                    pg.fill(
                        "textarea[name=question]",
                        "Which customers marked active have gone quiet?",
                    )
                    pg.click("button:has-text('Draft brief')")
                    pg.wait_for_selector("text=Review the draft")
                    pg.mouse.move(0, 0)
                    pg.evaluate("document.activeElement && document.activeElement.blur()")
                    pg.wait_for_timeout(300)
                if name == "sweep-ring":  # #97: the detectors' list, open
                    pg.click(".det-sq-btn")
                    pg.wait_for_timeout(300)
                if name == "summary-open":
                    pg.click(".sum-row summary")
                    pg.wait_for_timeout(300)
                pg.screenshot(
                    path=str(OUT / f"{name}{tag}.png"),
                    full_page=not shot and not name.startswith("sweep"),
                )
            ctx.close()
        b.close()
    server.should_exit = True
    th.join(5)
    print("previews in", OUT)


def _ask_sample(env, svc, cid) -> None:  # type: ignore[no-untyped-def]
    """A question asked on the sample night (no model here: the card alone), then a formatted
    answer put on it as a model would word it, so the preview shows the formatting. Preview
    only; the figures in it are the card's."""
    from othor.setup.models import ChatMessage
    from tests.web.test_in_place import _post

    back = "/home?panel=othor&subject=ask"
    _post(
        env,
        f"http://indigothread.{BASE}",
        "/othor/reply",
        {"subject": "ask", "text": "update on numbers", "back": back},
    )
    with svc.sf() as s, tenant_scope(cid):
        m = s.scalars(
            sa.select(ChatMessage)
            .where(ChatMessage.role == "ai")
            .order_by(ChatMessage.created_at.desc())
        ).first()
        if m is None:
            return
        facts = dict((m.proposal or {}).get("card", {}).get("facts") or [])
        nr = facts.get("Net revenue", "").split(" · ")[0]
        inv = facts.get("Invoiced value", "").split(" · ")[0]
        m.text = (
            f"Net revenue is **{nr}** on invoiced value of {inv}.\n\n"
            "The bigger story is cash: what's still owed is well above what came in."
        )
        s.commit()


def _merge_draft(svc, cid) -> str:  # type: ignore[no-untyped-def]
    """The night's same-number proposal Summary's card opens (Summary 1.1)."""
    from othor.engine.models import Proposal

    with svc.sf() as s, tenant_scope(cid):
        pid = s.scalars(
            sa.select(Proposal.id)
            .where(Proposal.kind == "duplicate_metric")
            .order_by(Proposal.fingerprint)
        ).first()
    return str(pid or "")
