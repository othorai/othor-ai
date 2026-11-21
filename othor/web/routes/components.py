# SPDX-License-Identifier: Apache-2.0
"""/_components: every shared component and the board pages 9a–9c, for review (not in prod)."""

from __future__ import annotations

import datetime
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from othor.web.components_demo import demo_specs
from othor.web.templating import asset, templates

router = APIRouter(prefix="/_components", include_in_schema=False)
BOARDS = {"9a", "9b", "9c", "states", "stress"}  # the website's board moved with it (M20.2)


@router.get("", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    from othor.web.context import Viewer

    demo_user = Viewer(uuid.UUID(int=1), "Uday Kamath", "uk@indigothread.example", "owner")
    return templates.TemplateResponse(
        request,
        "components_demo/index.html",
        {
            "specs": demo_specs(),
            "demo_user": demo_user,
            "demo_date": datetime.date(2026, 11, 1),
            "level_demo": level_demo(),
        },
    )


def level_demo() -> dict[str, object]:
    """Business Model 2.4: the level card as on a Business Model with no owners, open."""
    from othor.ontology.levels import ITEMS, Item, Levels

    done = {"sources", "numbers", "rules", "goal", "breakdowns", "parts", "headline"}
    items = [
        Item(
            k,
            lv,
            label,
            unlocks,
            k in done,
            "3 rules" if k == "rules" else "",
            say="The Head of Sales owns Net revenue and Orders" if k == "owners" else "",
        )
        for k, lv, label, unlocks in ITEMS
    ]
    v = Levels(0, items).view()
    v["groups"][0]["rows"][4]["drafts"] = [
        {
            "subject": "ontology:level:owners",
            "text": "Add the Head of Sales and the Head of Finance as owners",
            "why": "Named in your business message",
            "n": 2,
        }
    ]
    return {**v, "open": True, "can_find": True, "has_drafts": True}


@router.get("/{board}", response_class=HTMLResponse)
def board(request: Request, board: str) -> HTMLResponse:
    if board not in BOARDS:
        raise HTTPException(404)
    from othor.web.components_demo import stress_ctx

    ctx = {"specs": demo_specs(), **(stress_ctx() if board == "stress" else {})}
    return templates.TemplateResponse(request, f"components_demo/{board}.html", ctx)


@router.get("/mail/8a", response_class=HTMLResponse)
def mail_8a(request: Request) -> HTMLResponse:
    """The sign-in code email (8a) as rendered, for /compare-screen."""
    from othor.mail.messages import sign_in_code
    from othor.web.context import services

    msg = sign_in_code(
        to="uk@indigothread.example",
        code="482913",
        company_name="Indigo Thread Co.",
        slug="indigothread",
        settings=services(request).settings,
    )
    sender = msg.sender.replace("<", "&lt;")
    meta = (
        f'<div class="mail-meta"><p>Subject: <b>{msg.subject}</b> · From: {sender}</p>'
        "<p>Preheader: Expires in 10 minutes. "
        "If you didn’t request it, ignore this email.</p></div>"
    )
    head = (
        '<link rel="stylesheet" href="/static/css/fonts.css">'
        f'<link rel="stylesheet" href="{asset("app.css")}">'
    )
    return HTMLResponse(
        msg.html.replace("<body", head + "<body", 1).replace(
            '<div style="display:none', meta + '<div style="display:none', 1
        )
    )


SCREENS = {"1c", "1d", "1f", "1h", "2a"}


@router.get("/screens/{board}", response_class=HTMLResponse)
def screen(request: Request, board: str) -> HTMLResponse:
    """States reached by a POST, rendered from the real templates with fixed values."""
    from types import SimpleNamespace

    from othor.web.routes.auth import CURRENCIES, FY_MONTHS, TIMEZONES, page

    if board not in SCREENS:
        raise HTTPException(404)
    company = SimpleNamespace(name="Indigo Thread Co.", slug="indigothread")
    code_ctx = {
        "email": "uday@indigothread.example",
        "action": "#",
        "resend_action": "#",
        "back": "#",
        "state": "",
        "attempts_left": 0,
        "digits": ["4", "8", "2", "", "", ""],
        "resend_in": 42,
    }
    if board == "1c":
        return page(request, "pages/code.html", {**code_ctx, "crumb": None})
    if board == "1f":
        return page(
            request, "pages/code.html", {**code_ctx, "crumb": "othor.localhost/signup/verify"}
        )
    if board == "1d":
        return page(request, "components_demo/1d.html", {**code_ctx, "crumb": None})
    if board == "1h":
        return page(
            request,
            "pages/company_login.html",
            {
                "company": company,
                "initials": "BI",
                "host": "indigothread.othor.localhost",
                "signed_out": False,
                "email": "ravi@indigothread.example",
                "error": "This email isn’t part of Indigo Thread Co..",
            },
        )
    return page(
        request,
        "pages/address.html",
        {
            "company_name": "Indigo Thread Co.",
            "problem": None,
            "crumb": "othor.localhost/signup/address",
            "v": {
                "slug": "indigothread-demo",
                "website": "https://indigothread.example",
                "fy": "4",
                "currency": "INR",
                "tz": "Asia/Kolkata",
            },
            "fy_months": FY_MONTHS,
            "currencies": CURRENCIES,
            "timezones": TIMEZONES,
        },
    )
