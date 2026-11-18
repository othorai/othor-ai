# SPDX-License-Identifier: Apache-2.0
"""Sign-in (1a–1h), signup (1e, 1f, 2a), the signup handoff, and sign out (docs/01 §2).

The root host (othor.ai) asks for the company address and runs signup; the company host
(<slug>.othor.ai) signs owners in. Every outcome writes an event in the company's log.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from othor.core import auth, fmt
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.core.models import AppUser, Company, Membership, SetupState
from othor.core.signing import BadSignature
from othor.core.tenancy import tenant_scope
from othor.mail.messages import sign_in_code
from othor.web.context import (
    RESERVED,
    SLUG_RE,
    Services,
    Site,
    company_url,
    initials,
    resolve_site,
    root_url,
    services,
)
from othor.web.errors import rate_limit
from othor.web.security import check_csrf, clear_cookie, session_cookie_name, set_cookie
from othor.web.templating import templates

router = APIRouter(include_in_schema=False)

LOGIN_COOKIE = "othor_login"  # company host: which email is signing in
SIGNUP_COOKIE = "othor_signup"  # root host: who is signing up, and whether the code passed
RECENT_COOKIE = "othor_recent"  # root host: recent company addresses (1a chips)
STEP_ORDER = ["company", "upload", "business", "sources", "metrics", "first-run"]
FY_MONTHS = [
    (str(m), name)
    for m, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
]
CURRENCIES = [("INR", "INR (₹), lakh/crore"), ("USD", "USD ($), thousands/millions")]  # M15.6
# a currency's words for the settings line (onboarding 2a, the Ontology): "₹ in lakh and crore"
CURRENCY_LINE = {"INR": "₹ in lakh and crore", "USD": "$ in thousands and millions"}
# (value, name, the zone and offset under it): the listbox shows the name with its zone under it
TIMEZONES = [
    ("Asia/Kolkata", "India · IST", "Asia/Kolkata · UTC+05:30"),
    ("Asia/Dubai", "Dubai", "Asia/Dubai · UTC+04:00"),
    ("Asia/Singapore", "Singapore", "Asia/Singapore · UTC+08:00"),
    ("Europe/London", "London", "Europe/London · UTC+00:00"),
    ("UTC", "UTC", "Coordinated Universal Time"),
    # M15.6: the seven US zones (the abbreviation follows the date: EST in winter, EDT in summer)
    ("America/New_York", "US Eastern · New York", "America/New_York · UTC−05:00 / −04:00"),
    ("America/Chicago", "US Central · Chicago", "America/Chicago · UTC−06:00 / −05:00"),
    ("America/Denver", "US Mountain · Denver", "America/Denver · UTC−07:00 / −06:00"),
    ("America/Phoenix", "US Arizona · Phoenix", "America/Phoenix · UTC−07:00, no daylight saving"),
    ("America/Los_Angeles", "US Pacific · Los Angeles", "America/Los_Angeles · UTC−08:00 / −07:00"),
    ("America/Anchorage", "US Alaska · Anchorage", "America/Anchorage · UTC−09:00 / −08:00"),
    (
        "Pacific/Honolulu",
        "US Hawaii · Honolulu",
        "Pacific/Honolulu · UTC−10:00, no daylight saving",
    ),
]
TZ_NOTE = "The nightly run starts at 2:00 AM and the brief goes out at 6:00 AM in this time zone."


PAUSED_ON = ("home", "brief", "sweep")  # the pages a stopped worker leaves empty (M11b §6)


def paused_text(request: Request, company: Any) -> str | None:
    """The banner's "since Wed 4 Nov 14:30 IST", when the heartbeat is over 10 minutes old."""
    from zoneinfo import ZoneInfo

    from othor.jobs import heartbeat

    svc = services(request)
    at = heartbeat.paused_since(svc.sf, svc.clock())
    if at is None:
        return None
    local = at.astimezone(ZoneInfo(company.timezone or "Asia/Kolkata"))
    return f"since {fmt.date_short(local.date())} {fmt.clock(at)}"


def page(request: Request, name: str, ctx: dict[str, Any], status: int = 200) -> HTMLResponse:
    # times on the page are shown in the company's zone (fmt.clock); the viewer's from v0.15
    company = ctx.get("company") or getattr(getattr(request.state, "site", None), "company", None)
    fmt.set_display_tz(getattr(company, "timezone", None))
    fmt.set_money_style(getattr(company, "currency", None))  # M15.6: ₹ or $, per company
    if company is not None:  # v0.25 M25.1: Settings › Charts
        fmt.set_chart_style(fmt.style_of(company))
    base = {
        "csrf_token": request.state.csrf,
        "root_url": root_url(request, "/"),
        "base_domain": services(request).settings.base_domain,
        "app_env": services(request).settings.env,  # the site's footer: Performance only when live
    }
    if ctx.get("nav") in PAUSED_ON and company is not None:
        base["paused"] = paused_text(request, company)
    if request.query_params.get("panel") == "contact" and ctx.get("user") and company:
        base["contact_panel"] = contact_view(request, company, ctx["user"])
    # the one Othor AI chat (0.26); "questions" is the old Questions panel's link (emails, history)
    old_ontology = request.query_params.get("panel") == "chat" and request.url.path == "/ontology"
    if (
        (request.query_params.get("panel") in ("othor", "questions") or old_ontology)
        and ctx.get("user")
        and company
    ):
        base["othor_panel"] = othor_view(request, company)
    return templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status)


def _subject(request: Request) -> str:
    """The chat's subject; the old panels' links open it on theirs ("questions"; the Business
    Model's panel=chat with page · add · metric:<key>)."""
    q = request.query_params
    if q.get("panel") == "questions":
        return "questions"
    if q.get("panel") == "chat":
        old = q.get("subject") or "page"
        return "ontology" if old == "page" else f"ontology:{old}"
    return q.get("subject") or "ask"


def othor_view(request: Request, company: Any) -> dict[str, Any] | None:
    """The Othor AI chat in the side panel, on whatever page it opened (Chat 1.0, 0.26)."""
    from othor.core.localtime import local_date
    from othor.core.tenancy import tenant_scope
    from othor.web.othor_chat_view import view

    svc = services(request)
    q = request.query_params
    with svc.sf() as s, tenant_scope(company.id):
        return view(
            s,
            request,
            _subject(request),
            editing=q.get("edit") == "1",
            today=local_date(company, svc.clock()),
        )


def contact_view(request: Request, company: Any, user: Any) -> dict[str, Any] | None:
    """Contact us in the app's side panel (owner, 17 Nov 2026), on whatever page it opened: the
    topic from the link, the owner already known. None without the website (self-hosted)."""
    from urllib.parse import urlencode

    from othor.core import contact

    svc = services(request)
    if not contact.available(svc.settings):
        return None
    q = request.query_params
    topic = contact.topic(q.get("topic"))
    rest = [(k, v) for k, v in q.multi_items() if k not in ("panel", "topic", "sent")]
    here = request.url.path
    return {
        "line": dict(contact.TOPICS)[topic],
        "form": {"topic": topic},
        "sent": q.get("sent") == "1",
        "close": here + ("?" + urlencode(rest) if rest else ""),
        "back": here + "?" + urlencode([*rest, ("panel", "contact"), ("topic", topic)]),
        "name": user.name or user.email,
        "email": user.email,
        "company": company.name,
        "to": svc.settings.access_request_to,
    }


def not_found(
    request: Request,
    site: Site | None = None,
    what: str | None = None,
    back: tuple[str, str] | None = None,
) -> Response:
    """The 404 (9a): what isn't there and one way back; in the app shell when signed in.
    Another company's object takes this same path, so it reads exactly like a missing one."""
    from othor.web.errors import Missing, not_found_page

    if site is not None:
        request.state.site = site
    missing = None
    if what:
        label, href = back or ("Home", "/home")
        missing = Missing(what, label, href)
    return not_found_page(request, missing)


def event(
    svc: Services,
    company_id: uuid.UUID,
    action: str,
    *,
    actor: str | uuid.UUID = SYSTEM_ACTOR,
    object_type: str = "company",
    object_id: str | uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> uuid.UUID:
    with svc.sf() as s, tenant_scope(company_id):
        ev = record_event(
            s,
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=object_id or company_id,
            before=before,
            after=after,
        )
        s.commit()
        return ev.id


def resume_path(svc: Services, company_id: uuid.UUID) -> str:
    with svc.sf() as s, tenant_scope(company_id):
        state = s.scalars(sa.select(SetupState)).first()
    step = state.current_step if state else "company"
    return "/home" if step == "done" else f"/setup/{step}"


# ---------------------------------------------------------------------------------------------
# Root host: the public landing page (othor.ai, M11a) and the company address (1a)


def landing(request: Request) -> Response | None:
    """othor.ai (M11d): a website plug-in's landing on the root host (M20.2); None without one."""
    from othor.core import plugins

    page_fn = plugins.root_page()
    return page_fn(request) if page_fn is not None else None


@router.get("/", response_class=HTMLResponse)
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, signed_out: int = 0) -> Response:
    site = resolve_site(request)
    if site.kind == "unknown":
        return not_found(request, site)
    if site.is_root and services(request).settings.tenancy == "single":
        return RedirectResponse("/signup", 303)  # one company per install, not set up yet
    if site.is_root:
        if request.url.path == "/" and request.query_params.get("signed_out"):
            return RedirectResponse("/login", 303)
        public = request.url.path == "/" and services(request).settings.public_site
        if public and (shown := landing(request)) is not None:
            return shown
        return root_address(request)
    assert site.company is not None
    svc = services(request)
    if request.url.path == "/":
        sess = svc.auth.session_for(request.cookies.get(session_cookie_name(request)))
        if sess and sess.company_id == site.company.id:
            return RedirectResponse(resume_path(svc, site.company.id), 303)
        return RedirectResponse("/login", 303)
    return company_login(request, site, signed_out=bool(signed_out))


def root_address(request: Request, error: str | None = None, value: str = "") -> HTMLResponse:
    recent = [r for r in (request.cookies.get(RECENT_COOKIE) or "").split(",") if SLUG_RE.match(r)][
        :3
    ]
    return page(
        request,
        "pages/root_login.html",
        {"recent": recent, "error": error, "value": value},
        status=200 if not error else 400,
    )


@router.post(
    "/login",
    response_class=HTMLResponse,
    dependencies=[Depends(check_csrf), Depends(rate_limit("auth"))],
)
async def login_submit(request: Request) -> Response:
    site = resolve_site(request)
    form = await request.form()
    if site.is_root:
        slug = (
            str(form.get("slug", ""))
            .strip()
            .lower()
            .removesuffix("." + services(request).settings.base_domain)
        )
        svc = services(request)
        with svc.sf() as s:
            exists = s.scalars(
                sa.select(Company.id).where(Company.slug == slug, Company.deleted_at.is_(None))
            ).first()
        if not exists:
            return root_address(
                request,
                error=f"There’s no company at {slug or '…'}.{svc.settings.base_domain}.",
                value=slug,
            )
        resp = RedirectResponse(company_url(request, slug, "/login"), 303)
        recent = [slug] + [
            r for r in (request.cookies.get(RECENT_COOKIE) or "").split(",") if r and r != slug
        ]
        set_cookie(request, resp, RECENT_COOKIE, ",".join(recent[:3]), max_age=60 * 60 * 24 * 365)
        return resp
    if site.company is None:
        return not_found(request, site)
    return send_signin_code(request, site, str(form.get("email", "")))


# ---------------------------------------------------------------------------------------------
# Company host: email (1b, 1g, 1h) and code (1c, 1d)


def company_login(
    request: Request,
    site: Site,
    *,
    signed_out: bool = False,
    email: str = "",
    error: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    assert site.company is not None
    return page(
        request,
        "pages/company_login.html",
        {
            "company": site.company,
            "initials": initials(site.company.name),
            "host": site.host,
            "signed_out": signed_out,
            "email": email,
            "error": error,
        },
        status=status,
    )


def _is_member(svc: Services, company_id: uuid.UUID, email: str) -> AppUser | None:
    with svc.sf() as s, tenant_scope(company_id):
        return s.scalars(
            sa.select(AppUser)
            .join(Membership, Membership.user_id == AppUser.id)
            .where(AppUser.email == email)
        ).first()


def send_signin_code(request: Request, site: Site, email: str) -> Response:
    assert site.company is not None
    svc = services(request)
    email = email.strip().lower()
    if "@" not in email:
        return company_login(request, site, email=email, error="Enter your work email.", status=400)
    user = _is_member(svc, site.company.id, email)
    if user is None:
        event(
            svc,
            site.company.id,
            "auth.not_a_member",
            object_type="login",
            object_id=email.split("@")[-1],
        )
        return company_login(
            request,
            site,
            email=email,
            status=400,
            error=f"This email isn’t part of {site.company.name}.",
        )
    resp = RedirectResponse("/login/code", 303)
    set_cookie(
        request,
        resp,
        LOGIN_COOKIE,
        svc.signer("login").dumps({"email": email}, svc.clock()),
        max_age=60 * 30,
    )
    try:
        issued = svc.auth.issue_code(email, "signin", company_id=site.company.id)
    except (auth.ResendTooSoon, auth.Locked, auth.TooManyCodes):
        return resp  # the code page shows the cooldown or the lock
    svc.relay.send(
        sign_in_code(
            to=email,
            code=issued.code,
            company_name=site.company.name,
            slug=site.company.slug,
            settings=svc.settings,
        )
    )
    event(
        svc, site.company.id, "auth.code_sent", actor=user.id, object_type="user", object_id=user.id
    )
    return resp


def _login_email(request: Request, svc: Services) -> str | None:
    try:
        return str(
            svc.signer("login").loads(request.cookies.get(LOGIN_COOKIE), svc.clock(), 60 * 30)[
                "email"
            ]
        )
    except (BadSignature, KeyError):
        return None


def code_page(
    request: Request,
    *,
    email: str,
    action: str,
    resend_action: str,
    back: str,
    crumb: str | None,
    state: str = "",
    attempts_left: int = 0,
    digits: list[str] | None = None,
    resend_in: int = 0,
    status: int = 200,
) -> HTMLResponse:
    return page(
        request,
        "pages/code.html",
        {
            "email": email,
            "action": action,
            "resend_action": resend_action,
            "back": back,
            "crumb": crumb,
            "state": state,
            "attempts_left": attempts_left,
            "digits": digits or [""] * 6,
            "resend_in": resend_in,
        },
        status=status,
    )


@router.get("/login/code", response_class=HTMLResponse)
def login_code_page(request: Request) -> Response:
    site = resolve_site(request)
    if site.company is None:
        return (
            not_found(request, site) if site.kind == "unknown" else RedirectResponse("/login", 303)
        )
    svc = services(request)
    email = _login_email(request, svc)
    if not email:
        return RedirectResponse("/login", 303)
    return code_page(
        request,
        email=email,
        action="/login/code",
        resend_action="/login/resend",
        back="/login",
        crumb=None,
        resend_in=svc.auth.resend_in(email, "signin", site.company.id),
    )


def _digits(form: Any) -> str:
    joined = "".join(str(form.get(f"code{i}", "")) for i in range(6))
    return "".join(ch for ch in (joined or str(form.get("code", ""))) if ch.isdigit())[:6]


@router.post(
    "/login/code",
    response_class=HTMLResponse,
    dependencies=[Depends(check_csrf), Depends(rate_limit("codes"))],
)
async def login_code_submit(request: Request) -> Response:
    site = resolve_site(request)
    if site.company is None:
        return RedirectResponse("/login", 303)
    svc = services(request)
    email = _login_email(request, svc)
    if not email:
        return RedirectResponse("/login", 303)
    code = _digits(await request.form())
    common = {
        "email": email,
        "action": "/login/code",
        "resend_action": "/login/resend",
        "back": "/login",
        "crumb": None,
    }
    try:
        svc.auth.verify_code(email, "signin", code, company_id=site.company.id)
    except auth.WrongCode as e:
        event(
            svc,
            site.company.id,
            "auth.code_failed",
            object_type="login",
            object_id=email.split("@")[-1],
        )
        return code_page(
            request,
            **common,
            state="wrong",
            attempts_left=e.attempts_left,
            digits=list(code.ljust(6))[:6],
            status=400,
        )
    except auth.Locked:
        event(
            svc,
            site.company.id,
            "auth.code_locked",
            object_type="login",
            object_id=email.split("@")[-1],
        )
        return code_page(request, **common, state="locked", status=429)
    except (auth.Expired, auth.NoCode):
        return code_page(request, **common, state="expired", status=400)
    user = _is_member(svc, site.company.id, email)
    if user is None:  # removed between code and sign-in
        return company_login(
            request,
            site,
            email=email,
            status=400,
            error=f"This email isn’t part of {site.company.name}.",
        )
    token = svc.auth.create_session(user.id, site.company.id, request.headers.get("user-agent"))
    event(
        svc, site.company.id, "auth.signed_in", actor=user.id, object_type="user", object_id=user.id
    )
    resp = RedirectResponse(resume_path(svc, site.company.id), 303)
    set_cookie(
        request,
        resp,
        session_cookie_name(request),
        token,
        max_age=int(auth.SESSION_TTL.total_seconds()),
    )
    clear_cookie(request, resp, LOGIN_COOKIE)
    return resp


@router.post(
    "/login/resend",
    response_class=HTMLResponse,
    dependencies=[Depends(check_csrf), Depends(rate_limit("codes"))],
)
def login_resend(request: Request) -> Response:
    site = resolve_site(request)
    if site.company is None:
        return RedirectResponse("/login", 303)
    svc = services(request)
    email = _login_email(request, svc)
    if not email:
        return RedirectResponse("/login", 303)
    common = {
        "email": email,
        "action": "/login/code",
        "resend_action": "/login/resend",
        "back": "/login",
        "crumb": None,
    }
    try:
        issued = svc.auth.issue_code(email, "signin", company_id=site.company.id)
    except auth.ResendTooSoon as e:
        return code_page(request, **common, state="cooldown", resend_in=e.seconds_left)
    except (auth.Locked, auth.TooManyCodes):
        return code_page(request, **common, state="locked", status=429)
    svc.relay.send(
        sign_in_code(
            to=email,
            code=issued.code,
            company_name=site.company.name,
            slug=site.company.slug,
            settings=svc.settings,
        )
    )
    return code_page(
        request, **common, state="cooldown", resend_in=int(auth.RESEND_AFTER.total_seconds())
    )


# ---------------------------------------------------------------------------------------------
# Signup on the root host: 1e → 1f → 2a → handoff to the company host


def _signup(request: Request, svc: Services) -> dict[str, Any] | None:
    try:
        return svc.signer("signup").loads(request.cookies.get(SIGNUP_COOKIE), svc.clock(), 60 * 60)
    except BadSignature:
        return None


def _set_signup(request: Request, resp: Response, svc: Services, data: dict[str, Any]) -> None:
    set_cookie(
        request, resp, SIGNUP_COOKIE, svc.signer("signup").dumps(data, svc.clock()), max_age=60 * 60
    )


def _invite_only(request: Request, state: str, status: int = 200) -> HTMLResponse:
    """Sign-up by invitation (M11d part C): without a usable invitation, the page explains and
    offers Request access. `state`: invite_only (no code) or bad_invite (used, expired, revoked
    or unknown; the page never says which)."""
    svc = services(request)
    return page(
        request,
        "pages/signup.html",
        # Request access lives with the website (hosted only, M20.2)
        {"state": state, "form": {}, "errors": {}, "access_form": _website(svc)},
        status=status,
    )


def _website(svc: Services) -> bool:
    """The public website is on and a website plug-in is installed (M20.2)."""
    from othor.core import plugins

    return bool(svc.settings.public_site and plugins.root_page() is not None)


def _licence_refusal(request: Request) -> Response | None:
    """Several companies on one install (subdomains) is an enterprise feature (M20.3): without a
    licence that allows it, or once its company limit is reached, no new company is set up.
    Existing companies are never affected (owner, 16 Nov 2026)."""
    from othor.core import licence

    svc = services(request)
    if svc.settings.tenancy != "multi":
        return None
    st = licence.status(svc.settings, svc.clock().date())
    limit = st.limits.get("companies")
    full = False
    if st.allows("multi_company") and limit is not None:
        with svc.sf() as s:
            n = s.scalar(
                sa.select(sa.func.count()).select_from(Company).where(Company.deleted_at.is_(None))
            )
        full = int(n or 0) >= limit
    if st.allows("multi_company") and not full:
        return None
    why = "its company limit is reached" if full else "its licence doesn't allow another company"
    logging.getLogger("othor.web").warning("sign-up refused: %s (licence %s)", why, st.state)
    return page(
        request,
        "pages/single_taken.html",
        {"title": "No new companies here", "text": f"This Othor AI can't add a company: {why}."},
        status=403,
    )


def _single_taken(request: Request, site: Site) -> Response | None:
    """One company per install (M20.1): once it exists, sign-up is closed."""
    if services(request).settings.tenancy != "single" or site.company is None:
        return None
    return page(request, "pages/single_taken.html", {"company_name": site.company.name}, status=409)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, invite: str = "") -> Response:
    site = resolve_site(request)
    if (taken := _single_taken(request, site)) is not None:
        return taken
    if (refused := _licence_refusal(request)) is not None:
        return refused
    if not site.is_root:
        q = f"?invite={invite}" if invite else ""
        return RedirectResponse(root_url(request, "/signup" + q), 303)
    svc = services(request)
    data = _signup(request, svc) or {}
    if svc.settings.signup == "invite":
        code = invite or data.get("invite", "")
        if not code:
            return _invite_only(request, "invite_only")
        try:
            inv = svc.auth.check_invitation(code)
        except auth.BadInvitation:
            return _invite_only(request, "bad_invite")
        if code != data.get("invite"):  # a new invitation starts a fresh form
            data = {"email": inv.email, "company": inv.company_name or ""}
        data = {**data, "invite": code}
    return page(request, "pages/signup.html", {"form": data, "errors": {}})


@router.post(
    "/signup",
    response_class=HTMLResponse,
    dependencies=[Depends(check_csrf), Depends(rate_limit("codes"))],
)
def signup_submit(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    company: str = Form(""),
    invite: str = Form(""),
) -> Response:
    svc = services(request)
    name, email, company = name.strip(), email.strip().lower(), company.strip()
    errors = {}
    if not name:
        errors["name"] = "Enter your name."
    if "@" not in email or "." not in email.split("@")[-1]:
        errors["email"] = "Enter your work email."
    if not company:
        errors["company"] = "Enter the company name."
    form = {"name": name, "email": email, "company": company}
    if svc.settings.signup == "invite":
        form["invite"] = invite
        try:
            svc.auth.check_invitation(invite, email if "email" not in errors else None)
        except auth.BadInvitation:
            return _invite_only(request, "bad_invite", status=400)
        except auth.WrongInviteEmail:
            errors["email"] = "Use the email address the invitation was sent to."
    if errors:
        return page(request, "pages/signup.html", {"form": form, "errors": errors}, status=400)
    resp = RedirectResponse("/signup/verify", 303)
    _set_signup(request, resp, svc, {**form, "verified": False})
    try:
        issued = svc.auth.issue_code(email, "signup", payload=form)
    except (auth.ResendTooSoon, auth.Locked, auth.TooManyCodes):
        return resp
    svc.relay.send(
        sign_in_code(
            to=email, code=issued.code, company_name=company, slug=None, settings=svc.settings
        )
    )
    return resp


@router.get("/signup/verify", response_class=HTMLResponse)
def signup_verify_page(request: Request) -> Response:
    svc = services(request)
    data = _signup(request, svc)
    if not data:
        return RedirectResponse("/signup", 303)
    return code_page(
        request,
        email=data["email"],
        action="/signup/verify",
        resend_action="/signup/resend",
        back="/signup",
        crumb=f"{svc.settings.base_domain}/signup/verify",
        resend_in=svc.auth.resend_in(data["email"], "signup"),
    )


@router.post(
    "/signup/verify",
    response_class=HTMLResponse,
    dependencies=[Depends(check_csrf), Depends(rate_limit("codes"))],
)
async def signup_verify_submit(request: Request) -> Response:
    svc = services(request)
    data = _signup(request, svc)
    if not data:
        return RedirectResponse("/signup", 303)
    code = _digits(await request.form())
    common = {
        "email": data["email"],
        "action": "/signup/verify",
        "resend_action": "/signup/resend",
        "back": "/signup",
        "crumb": f"{svc.settings.base_domain}/signup/verify",
    }
    try:
        svc.auth.verify_code(data["email"], "signup", code)
    except auth.WrongCode as e:
        return code_page(
            request,
            **common,
            state="wrong",
            attempts_left=e.attempts_left,
            digits=list(code.ljust(6))[:6],
            status=400,
        )
    except auth.Locked:
        return code_page(request, **common, state="locked", status=429)
    except (auth.Expired, auth.NoCode):
        return code_page(request, **common, state="expired", status=400)
    resp = RedirectResponse("/signup/address", 303)
    _set_signup(request, resp, svc, {**data, "verified": True})
    return resp


@router.post(
    "/signup/resend",
    response_class=HTMLResponse,
    dependencies=[Depends(check_csrf), Depends(rate_limit("codes"))],
)
def signup_resend(request: Request) -> Response:
    svc = services(request)
    data = _signup(request, svc)
    if not data:
        return RedirectResponse("/signup", 303)
    common = {
        "email": data["email"],
        "action": "/signup/verify",
        "resend_action": "/signup/resend",
        "back": "/signup",
        "crumb": f"{svc.settings.base_domain}/signup/verify",
    }
    try:
        issued = svc.auth.issue_code(data["email"], "signup", payload=data)
    except auth.ResendTooSoon as e:
        return code_page(request, **common, state="cooldown", resend_in=e.seconds_left)
    except (auth.Locked, auth.TooManyCodes):
        return code_page(request, **common, state="locked", status=429)
    svc.relay.send(
        sign_in_code(
            to=data["email"],
            code=issued.code,
            company_name=data["company"],
            slug=None,
            settings=svc.settings,
        )
    )
    return code_page(
        request, **common, state="cooldown", resend_in=int(auth.RESEND_AFTER.total_seconds())
    )


def suggest_slug(company: str) -> str:
    words = [w for w in "".join(c if c.isalnum() else " " for c in company.lower()).split() if w]
    return (words[0] if words else "company")[:30]


def slug_problem(svc: Services, slug: str) -> str | None:
    if not SLUG_RE.match(slug):
        return "Use 3–30 lowercase letters, numbers and hyphens."
    if slug in RESERVED:
        return f"{slug}.{svc.settings.base_domain} is reserved."
    with svc.sf() as s:
        if s.scalars(sa.select(Company.id).where(Company.slug == slug)).first():
            return f"{slug}.{svc.settings.base_domain} is taken."
    return None


def address_page(
    request: Request,
    data: dict[str, Any],
    values: dict[str, str],
    error: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    svc = services(request)
    slug = values["slug"]
    problem = error if error is not None else slug_problem(svc, slug)
    return page(
        request,
        "pages/address.html",
        {
            "company_name": data["company"],
            "v": values,
            "problem": problem,
            "single": svc.settings.tenancy == "single",
            "fy_months": FY_MONTHS,
            "currencies": CURRENCIES,
            "timezones": TIMEZONES,
            "tz_note": TZ_NOTE,
            # the one-line summary over the three settings (M11c); othor.js keeps it in step
            "fy_name": dict(FY_MONTHS).get(values.get("fy", "4"), "April"),
            "tz_name": next((t[1] for t in TIMEZONES if t[0] == values.get("tz")), "India · IST"),
            "cur_line": CURRENCY_LINE.get(values.get("currency") or "INR", CURRENCY_LINE["INR"]),
            "cur_lines": CURRENCY_LINE,
            # an error on one of them opens the fields
            "settings_open": bool(error) and values.get("fy") not in dict(FY_MONTHS),
        },
        status=status,
    )


@router.get("/signup/address", response_class=HTMLResponse)
def signup_address_page(request: Request) -> Response:
    data = _signup(request, services(request))
    if not data or not data.get("verified"):
        return RedirectResponse("/signup", 303)
    values = {
        "slug": suggest_slug(data["company"]),
        "website": f"https://{data['email'].split('@')[-1]}",
        "fy": "4",
        "currency": "INR",
        "tz": "Asia/Kolkata",
    }
    return address_page(request, data, values)


@router.get(
    "/signup/address/check",
    response_class=HTMLResponse,
    dependencies=[Depends(rate_limit("lookup"))],
)
def signup_address_check(request: Request, slug: str = "") -> HTMLResponse:
    svc = services(request)
    slug = slug.strip().lower()
    return templates.TemplateResponse(
        request,
        "partials/slug_check.html",
        {"slug": slug, "problem": slug_problem(svc, slug), "base_domain": svc.settings.base_domain},
    )


@router.post("/signup/address", response_class=HTMLResponse, dependencies=[Depends(check_csrf)])
def signup_address_submit(
    request: Request,
    slug: str = Form(""),
    website: str = Form(""),
    fy: str = Form("4"),
    currency: str = Form("INR"),
    tz: str = Form("Asia/Kolkata"),
) -> Response:
    svc = services(request)
    if (taken := _single_taken(request, resolve_site(request))) is not None:
        return taken
    if (refused := _licence_refusal(request)) is not None:
        return refused
    data = _signup(request, svc)
    if not data or not data.get("verified"):
        return RedirectResponse("/signup", 303)
    slug = slug.strip().lower()
    values = {"slug": slug, "website": website.strip(), "fy": fy, "currency": currency, "tz": tz}
    problem = slug_problem(svc, slug)
    if (
        problem
        or fy not in dict(FY_MONTHS)
        or currency not in dict(CURRENCIES)
        or tz not in {t[0] for t in TIMEZONES}
    ):
        return address_page(request, data, values, error=problem or "Check the fields.", status=400)
    now = svc.clock()
    with svc.sf() as s:
        user = s.scalars(sa.select(AppUser).where(AppUser.email == data["email"])).first()
        company = Company(
            slug=slug,
            name=data["company"],
            website=values["website"] or None,
            fiscal_year_start_month=int(fy),
            currency=currency,
            timezone=tz,
        )
        if user is None:
            user = AppUser(email=data["email"], name=data["name"])
            s.add(user)
        s.add(company)
        s.flush()
        company.workspace_id = company.id
        if svc.settings.signup == "open" and svc.settings.trial_days > 0:
            # open sign-up's free trial (owner, 16 Nov 2026): closed at its end, purged 30 days on
            company.trial_ends_at = now + dt.timedelta(days=svc.settings.trial_days)
        if svc.settings.signup == "invite":
            # used in this transaction: a second use fails, and a failed sign-up leaves it open
            try:
                svc.auth.use_invitation(s, data.get("invite", ""), data["email"], company.id)
            except (auth.BadInvitation, auth.WrongInviteEmail):
                s.rollback()
                refused = _invite_only(request, "bad_invite", status=400)
                clear_cookie(request, refused, SIGNUP_COOKIE)
                return refused
        with tenant_scope(company.id):
            s.add(Membership(company_id=company.id, user_id=user.id, role="owner"))
            s.add(
                SetupState(
                    company_id=company.id,
                    current_step="upload",
                    step_data={"company": {"completed_at": now.isoformat()}},
                )
            )
            after = {
                "slug": slug,
                "name": company.name,
                "website": company.website,
                "fiscal_year_start_month": int(fy),
                "currency": currency,
                "timezone": tz,
            }
            record_event(
                s,
                actor=user.id,
                action="company.created",
                object_type="company",
                object_id=company.id,
                after=after,
            )
            record_event(
                s,
                actor=user.id,
                action="user.signup",
                object_type="user",
                object_id=user.id,
                after={"email_domain": data["email"].split("@")[-1], "role": "owner"},
            )
            record_event(
                s,
                actor=user.id,
                action="onboarding.step_saved",
                object_type="setup_state",
                object_id=company.id,
                before={"step": "company"},
                after={"step": "upload"},
            )
            s.commit()
        user_id, company_id = user.id, company.id
    handoff = svc.auth.create_handoff(user_id, company_id)
    resp = RedirectResponse(company_url(request, slug, f"/auth/handoff?t={handoff}"), 303)
    clear_cookie(request, resp, SIGNUP_COOKIE)
    return resp


@router.get("/auth/handoff")
def handoff(request: Request, t: str = "") -> Response:
    site = resolve_site(request)
    if site.company is None:
        return not_found(request, site)
    svc = services(request)
    try:
        token = svc.auth.redeem_handoff(t, site.company.id, request.headers.get("user-agent"))
    except auth.BadHandoff:
        return RedirectResponse("/login", 303)
    sess = svc.auth.session_for(token)
    assert sess is not None
    event(
        svc,
        site.company.id,
        "auth.signed_in",
        actor=sess.user_id,
        object_type="user",
        object_id=sess.user_id,
    )
    resp = RedirectResponse(resume_path(svc, site.company.id), 303)
    set_cookie(
        request,
        resp,
        session_cookie_name(request),
        token,
        max_age=int(auth.SESSION_TTL.total_seconds()),
    )
    return resp


# ---------------------------------------------------------------------------------------------
# Sign out (account menu)


@router.post("/logout", dependencies=[Depends(check_csrf)])
def logout(request: Request) -> Response:
    return _logout(request, everywhere=False)


# No button in v0.1: it moves to the account settings page after v0.1 (docs/10 roadmap).
@router.post("/logout/everywhere", dependencies=[Depends(check_csrf)])
def logout_everywhere(request: Request) -> Response:
    return _logout(request, everywhere=True)


def _logout(request: Request, *, everywhere: bool) -> Response:
    svc = services(request)
    site = resolve_site(request)
    name = session_cookie_name(request)
    sess = svc.auth.session_for(request.cookies.get(name))
    if sess and site.company and sess.company_id == site.company.id:
        if everywhere:
            n = svc.auth.revoke_all(sess.user_id)
            event(
                svc,
                site.company.id,
                "auth.signed_out_everywhere",
                actor=sess.user_id,
                object_type="user",
                object_id=sess.user_id,
                after={"sessions_ended": n},
            )
        else:
            svc.auth.revoke(request.cookies.get(name))
            event(
                svc,
                site.company.id,
                "auth.signed_out",
                actor=sess.user_id,
                object_type="user",
                object_id=sess.user_id,
            )
    resp = RedirectResponse("/login?signed_out=1", 303)
    clear_cookie(request, resp, name)
    return resp


__all__ = ["STEP_ORDER", "router"]
