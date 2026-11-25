# SPDX-License-Identifier: Apache-2.0
"""Signed-in pages on the company host: onboarding steps, Settings (7a), and the nav pages
that show 9a empty states until their milestones. Another company's session gets 404."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from othor.core import fmt
from othor.core.contact import available as contact_available
from othor.core.events import record_event
from othor.core.models import AppUser, Company, Event, Membership, SetupState
from othor.core.tenancy import Repo, system_scope, tenant_scope
from othor.web.context import Site, Viewer, local_path, resolve_site, services
from othor.web.errors import rate_limit
from othor.web.routes.auth import (
    CURRENCIES,
    FY_MONTHS,
    STEP_ORDER,
    TIMEZONES,
    TZ_NOTE,
    not_found,
    page,
)
from othor.web.security import check_csrf, session_cookie_name

router = APIRouter(include_in_schema=False)

STEP_TITLES = {
    "company": "Company",
    "upload": "Upload",
    "business": "Business",
    "sources": "Sources",
    "metrics": "Metrics",
    "first-run": "First run",
}
# Big Picture's language (v0.19, M19.5): English, and Hindi as a try (owner, 14 Nov 2026)
REPORT_LANGUAGES = [("en", "English"), ("hi", "Hindi (a try)")]
# Settings › Charts (v0.25 M25.1, owner 18 Nov 2026): values on lines, Key points or none
CHART_VALUES = [("key", "Key points"), ("off", "Off")]
SETTINGS_FIELDS = {
    "fiscal_year_start_month": FY_MONTHS,
    "currency": CURRENCIES,
    "timezone": TIMEZONES,
    "report_language": REPORT_LANGUAGES,
    "chart_values": CHART_VALUES,
}


class Bounce(Exception):
    def __init__(self, response: Response) -> None:
        self.response = response


@dataclass
class Owner:
    site: Site
    company: Company
    viewer: Viewer
    session_id: uuid.UUID | None = None  # this device, on the Account page (M15.8)


def signed_in(request: Request) -> Owner:
    """The signed-in owner for this company host. Not signed in → /login. A session that
    belongs to another company → 404 (never reveal another company's objects)."""
    site = resolve_site(request)
    if site.company is None:
        raise Bounce(not_found(request, site))  # app pages live on company hosts only
    svc = services(request)
    sess = svc.auth.session_for(request.cookies.get(session_cookie_name(request)))
    if sess is None:
        raise Bounce(RedirectResponse("/login", 303))
    if sess.company_id != site.company.id:
        raise Bounce(not_found(request, site))
    from othor.core import fmt

    fmt.set_request_company(site.company)  # currency, charts, time zone: the route formats in them
    with svc.sf() as s:
        user = s.scalars(sa.select(AppUser).where(AppUser.id == sess.user_id)).one()
    with svc.sf() as s, tenant_scope(site.company.id):
        role = s.scalars(sa.select(Membership.role).where(Membership.user_id == user.id)).first()
    if role is None:  # no longer a member of this company
        raise Bounce(not_found(request, site))
    owner = Owner(site, site.company, Viewer.of(user, role), sess.id)
    request.state.owner = owner  # a 404 in this request renders in the app shell (M11a)
    return owner


def super_admin_only(request: Request) -> Owner:
    """signed_in, for the actions only the super admin may take (0.34): close the company,
    change the licence, export everything. An admin gets a 403, a toast for htmx."""
    o = signed_in(request)
    if o.viewer.is_super_admin:
        return o
    from othor.web.errors import is_htmx, toast

    if is_htmx(request):
        raise Bounce(toast(request, SUPER_ADMIN_ONLY, 403))
    raise Bounce(
        page(
            request,
            "pages/super_admin_only.html",
            {**shell_ctx(o, "settings"), "text": SUPER_ADMIN_ONLY},
            status=403,
        )
    )


SUPER_ADMIN_ONLY = (
    "Only the super admin can close the company, change the licence or export everything."
)


def shell_ctx(o: Owner, nav: str) -> dict[str, Any]:
    return {"company": o.company, "user": o.viewer, "nav": nav}


def _state(company_id: uuid.UUID, request: Request) -> SetupState | None:
    svc = services(request)
    with svc.sf() as s, tenant_scope(company_id):
        return s.scalars(sa.select(SetupState)).first()


# ---------------------------------------------------------------------------------------------
# Onboarding steps (shells until M2/M4 fill them)


@router.get("/setup/{step}", response_class=HTMLResponse)
def setup_step(request: Request, step: str, o: Owner = Depends(signed_in)) -> Response:
    if step not in STEP_ORDER:
        return not_found(request, o.site)
    state = _state(o.company.id, request)
    current = state.current_step if state else "company"
    if current == "done":
        return RedirectResponse("/home", 303)
    if STEP_ORDER.index(step) > STEP_ORDER.index(current):
        return RedirectResponse(f"/setup/{current}", 303)
    idx = STEP_ORDER.index(step)
    return page(
        request,
        "pages/setup_step.html",
        {
            **shell_ctx(o, "setup"),
            "step": step,
            "title": STEP_TITLES[step],
            "step_index": idx + 1,
            "back": f"/setup/{STEP_ORDER[idx - 1]}" if idx > 1 else None,
        },
    )


@router.post("/setup/{step}/continue", dependencies=[Depends(check_csrf)])
def setup_continue(
    request: Request,
    step: str,
    next_: str = Form("", alias="next"),
    o: Owner = Depends(signed_in),
) -> Response:
    if step not in STEP_ORDER:
        return not_found(request, o.site)
    svc = services(request)
    nxt = STEP_ORDER[STEP_ORDER.index(step) + 1] if step != STEP_ORDER[-1] else "done"
    with svc.sf() as s, tenant_scope(o.company.id):
        state = s.scalars(sa.select(SetupState)).first()
        if state is None:
            state = SetupState(current_step="company")
            s.add(state)
        before = state.current_step
        if STEP_ORDER.index(step) >= STEP_ORDER.index(
            before if before != "done" else STEP_ORDER[-1]
        ):
            state.current_step = nxt
            record_event(
                s,
                actor=o.viewer.id,
                action="onboarding.step_saved",
                object_type="setup_state",
                object_id=o.company.id,
                before={"step": before},
                after={"step": nxt},
            )
        s.commit()
    # "Open the brief" on the last step finishes setup too, then opens the brief (#22)
    done_to = "/brief" if next_ == "/brief" else "/home"
    r = RedirectResponse(done_to if nxt == "done" else f"/setup/{nxt}", 303)
    if step == "first-run" and not _first_brief_ready(request, o):
        # M11c: left before the first brief was written; the app's strip says when it is
        r.set_cookie("othor_first_run", "1", max_age=86400, httponly=True, samesite="lax")
    return r


def _first_brief_ready(request: Request, o: Owner) -> bool:
    from othor.web import run_view

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        view = run_view.first_run(s, svc.settings, o.company, svc.clock())
    return view is None or view.brief_ready or not view.polling


# ---------------------------------------------------------------------------------------------
# Settings (7a): every change writes an event with before/after; Undo writes another


def settings_page(request: Request, o: Owner, tab: str = "") -> HTMLResponse:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        export_v = _export_state(s, svc, o)
        s.expunge_all()
    from othor.web.routes.model_settings import pane_ctx

    return page(
        request,
        "pages/settings.html",
        {
            **shell_ctx(o, "settings"),
            **pane_ctx(request, o),  # Settings › AI Model (0.37)
            "company": company,
            "fy_months": FY_MONTHS,
            "currencies": CURRENCIES,
            "report_languages": REPORT_LANGUAGES,
            "chart_values": CHART_VALUES,
            "timezones": TIMEZONES,
            "tz_note": TZ_NOTE,
            "sender": f"brief@{svc.settings.mail_from_domain}",
            "export": export_v,
            "close": _close_ctx(svc, company),
            "address": _address(svc, company),
            "licence": licence_view(svc),
            "tab_line": _tab_line(request, company),
            "tab": tab,  # #106: the tab to open (the template checks it against its tabs)
        },
    )


def licence_view(svc: Any, error: str = "", saved: bool = False) -> dict[str, Any] | None:
    """Settings › Licence (M20.3): shown when the install has enterprise features or runs
    several companies; the key itself is never shown back."""
    from othor.core import licence, plugins

    if "ee" not in {p.name for p in plugins.loaded()} and svc.settings.tenancy != "multi":
        return None
    st = licence.status(svc.settings, svc.clock().date())
    return {
        "st": st,
        "features": sorted(st.features),
        "from_env": bool(str(svc.settings.licence or "").strip()),
        "error": error,
        "saved": saved,
    }


@router.post("/settings/licence", response_class=HTMLResponse, dependencies=[Depends(check_csrf)])
def settings_licence(
    request: Request, key: str = Form(""), o: Owner = Depends(super_admin_only)
) -> Response:
    """Paste a new licence key (M20.3): checked offline, saved to the data folder, an event."""
    from othor.core import licence

    svc = services(request)
    st = licence.check(key, svc.clock().date())
    if st.state not in ("valid", "grace") or str(svc.settings.licence or "").strip():
        why = (
            "This install's licence is set by the server (OTHOR_LICENCE)."
            if str(svc.settings.licence or "").strip()
            else f"That key isn't valid: {st.reason or st.state}."
        )
        return page(
            request,
            "partials/settings_licence.html",
            {"licence": licence_view(svc, why)},
            status=400,
        )
    path = svc.settings.licence_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(key.strip() + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)
    with svc.sf() as s, tenant_scope(o.company.id):
        record_event(
            s,
            actor=o.viewer.id,
            action="licence.updated",
            object_type="company",
            object_id=o.company.id,
            after={
                "id": st.id,
                "customer": st.customer,
                "edition": st.edition,
                "features": sorted(st.features),
                "expires": st.expires.isoformat() if st.expires else None,
            },
        )
        s.commit()
    return page(
        request, "partials/settings_licence.html", {"licence": licence_view(svc, saved=True)}
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_get(request: Request, tab: str = "", o: Owner = Depends(signed_in)) -> Response:
    return settings_page(request, o, tab)


def _apply(request: Request, o: Owner, changes: dict[str, Any], action: str) -> uuid.UUID | None:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        before = {k: getattr(company, k) for k in changes}
        changes = {k: v for k, v in changes.items() if before[k] != v}
        if not changes:
            return None
        for k, v in changes.items():
            setattr(company, k, v)
        ev = record_event(
            s,
            actor=o.viewer.id,
            action=action,
            object_type="company",
            object_id=company.id,
            before={k: before[k] for k in changes},
            after=changes,
        )
        s.commit()
        return ev.id


# M15.6: the settings a night's spec carries (`company_settings`): a change is a small Ontology
# version, so the next night compiles with it (a spec is compiled once per version)
SPEC_FIELDS = {
    "currency": ("currency", "Currency"),
    "fiscal_year_start_month": ("fiscal_year", "Fiscal year start"),
    "timezone": ("timezone", "Time zone"),
}


def _settings_version(request: Request, o: Owner, field: str, value: Any) -> None:
    """Once setup is confirmed: the setting as a business fact (`setting:<field>`, hidden from
    The business list), so a new Ontology version and a fresh spec carry it from the next night."""
    from othor.ontology import service
    from othor.ontology.compiler import compile_version

    kind, label = SPEC_FIELDS[field]
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if not service.confirmed(s):
            return
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        v = service.apply(
            s,
            [
                service.put(
                    "business_fact", f"setting:{field}", {"text": f"{label}: {value}", "kind": kind}
                )
            ],
            actor=str(o.viewer.id),
            reason=f"Settings: {label}",
        )
        if v is not None:
            compile_version(s, company, actor=str(o.viewer.id))
        s.commit()


@router.post("/settings", response_class=HTMLResponse, dependencies=[Depends(check_csrf)])
def settings_post(
    request: Request, field: str = Form(""), value: str = Form(""), o: Owner = Depends(signed_in)
) -> Response:
    if field in SETTINGS_FIELDS:
        if value not in {o[0] for o in SETTINGS_FIELDS[field]}:
            return HTMLResponse("Not a valid choice.", status_code=400)
        parsed: Any = int(value) if field == "fiscal_year_start_month" else value
    elif field in ("email_delivery", "chart_smooth"):
        parsed = value in ("on", "true", "1")
    else:
        return _plugin_setting(request, o, field, value)
    ev = _apply(request, o, {field: parsed}, "settings.changed")
    if ev is not None and field in SPEC_FIELDS:
        _settings_version(request, o, field, parsed)
    if field in ("email_delivery", "chart_smooth"):
        # a switch shows its own state; flipping it back undoes it, so no "saved · Undo" toast
        # (#17). The change is an event all the same.
        return Response(status_code=204)
    label = {
        "fiscal_year_start_month": "Fiscal year start",
        "currency": "Currency",
        "timezone": "Time zone",
        "report_language": "Big Picture language",
        "chart_values": "Values on lines",
        "email_delivery": "Email delivery",
    }[field]
    return page(request, "partials/toast_saved.html", {"text": f"{label} saved", "event_id": ev})


def _plugin_setting(request: Request, o: Owner, field: str, value: str) -> Response:
    """A setting a plug-in's fragment shows (M20.1): its handler saves it and returns
    (before, after); the event row is written here, as for the core's settings."""
    from othor.core import plugins

    handler = plugins.field_handler(field)
    if handler is None:
        return HTMLResponse("Unknown setting.", status_code=400)
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        try:
            before, after = handler(s, o.company, value, str(o.viewer.id))
        except ValueError as e:
            s.rollback()
            return HTMLResponse(str(e) or "Not a valid value.", status_code=400)
        record_event(
            s,
            actor=o.viewer.id,
            action="settings.changed",
            object_type="company",
            object_id=o.company.id,
            before={field: before},
            after={field: after},
        )
        s.commit()
    return Response(status_code=204)


@router.post(
    "/settings/undo/{event_id}", response_class=HTMLResponse, dependencies=[Depends(check_csrf)]
)
def settings_undo(request: Request, event_id: uuid.UUID, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        ev = Repo(s).get(Event, event_id)
    if ev is None or ev.action != "settings.changed" or not ev.before:
        return not_found(request, o.site)
    _apply(request, o, dict(ev.before), "settings.undone")
    for k, v in dict(ev.before).items():
        if k in SPEC_FIELDS:
            _settings_version(request, o, k, v)
    resp = HTMLResponse("")
    resp.headers["HX-Refresh"] = "true"
    return resp


# ---------------------------------------------------------------------------------------------
# The company's name (owner, 24 Nov 2026): fix a spelling at sign-up or a later change, in place.
# The web address (slug) stays, so links and emails keep working; every page, email, PDF and
# read-only link reads the name from the company row when it is made, so the next one has it.

NAME_MIN, NAME_MAX = 2, 80
ADMIN_ONLY = "Only the super admin or an admin can rename the company."


def admin_only(request: Request) -> Owner:
    """signed_in, for the super admin and admins; anyone else gets a 403 (a toast for htmx)."""
    o = signed_in(request)
    if o.viewer.is_admin:
        return o
    from othor.web.errors import toast

    raise Bounce(toast(request, ADMIN_ONLY, 403))


def _name_error(name: str) -> str | None:
    if not name:
        return "Enter the company name."
    if len(name) < NAME_MIN:
        return f"Use at least {NAME_MIN} characters."
    if len(name) > NAME_MAX:
        return f"Use {NAME_MAX} characters or fewer."
    return None


def _address(svc: Any, company: Company) -> str | None:
    """The company's web address, which a rename never changes (none on a one-company install,
    whose address is its own)."""
    if svc.settings.tenancy == "single":
        return None
    return f"{company.slug}.{svc.settings.base_domain}"


def _name_row(
    request: Request,
    o: Owner,
    company: Company,
    *,
    edit: bool = False,
    typed: str | None = None,
    error: str | None = None,
    saved: bool = False,
) -> HTMLResponse:
    """The name row; always 200, as htmx swaps only a 2xx (an error shows in the row)."""
    svc = services(request)
    return page(
        request,
        "partials/settings_name.html",
        {
            "company": company,
            "user": o.viewer,
            "address": _address(svc, company),
            "edit": edit,
            "typed": company.name if typed is None else typed,
            "error": error,
            "saved": saved,
            "close_oob": _close_ctx(svc, company) if saved and o.viewer.is_super_admin else None,
        },
    )


@router.get("/settings/name", response_class=HTMLResponse)
def settings_name(request: Request, edit: int = 0, o: Owner = Depends(signed_in)) -> Response:
    if edit and not o.viewer.is_admin:
        from othor.web.errors import toast

        return toast(request, ADMIN_ONLY, 403)
    return _name_row(request, o, o.company, edit=bool(edit))


@router.post("/settings/name", response_class=HTMLResponse, dependencies=[Depends(check_csrf)])
def settings_name_save(
    request: Request, name: str = Form("", alias="company_name"), o: Owner = Depends(admin_only)
) -> Response:
    """Rename the company: trimmed, 2–80 characters; an event with before and after."""
    name = " ".join(name.split())
    error = _name_error(name)
    if error:
        return _name_row(request, o, o.company, edit=True, typed=name, error=error)
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        before = company.name
        if name != before:
            company.name = name
            record_event(
                s,
                actor=o.viewer.id,
                action="company.renamed",
                object_type="company",
                object_id=company.id,
                before={"name": before},
                after={"name": name},
            )
            s.commit()
        s.refresh(company)
        s.expunge(company)
    return _name_row(request, o, company, saved=name != before)


# ---------------------------------------------------------------------------------------------
# The company's logo (M15.7): Settings uploads it; it is served on the company's own host (the
# sign-in page shows it before anyone signs in), by the hash of the PNG we wrote


def _set_logo(request: Request, o: Owner, sha: str | None, action: str) -> None:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        before = company.logo_sha
        company.logo_sha = sha
        company.tab_icon = None  # a new logo, or none: the tab follows it again
        record_event(
            s,
            actor=o.viewer.id,
            action=action,
            object_type="company",
            object_id=company.id,
            before={"logo_sha": before},
            after={"logo_sha": sha},
        )
        s.commit()


def _tab_line(request: Request, company: Company) -> dict[str, Any] | None:
    """Settings' line about the browser tab (Company identity 1.0): which icon it shows and, when
    the logo won't read at tab size (simple image rules, no model), the letters suggested."""
    from othor.core import logo
    from othor.web import tab_icon

    icon = tab_icon.choose(company)
    if icon is None:
        return None
    text = tab_icon.letters(company.name)
    reason = None
    if company.logo_sha and icon.kind == "logo":
        src = logo.read(services(request).settings, company.id, company.logo_sha)
        why = tab_icon.check(src) if src else None
        reason = tab_icon.REASONS[why] if why else None
    return {
        "kind": icon.kind,
        "url": icon.url(32),
        "letters": text,
        "reason": reason,
        "can_switch": bool(company.logo_sha and text),
    }


def _logo_row(
    request: Request, o: Owner, toast: str | None = None, icon_changed: bool = False
) -> Response:
    from othor.web import tab_icon

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        s.expunge_all()
    ctx = {
        "company": company,
        "toast": toast,
        "tab_line": _tab_line(request, company),
        # the tab's icon changes on this page at once (no reload): the head's links, swapped
        "tab_icon_oob": tab_icon.links(company) if icon_changed else None,
    }
    return page(request, "partials/settings_logo.html", ctx)


@router.post("/settings/logo", response_class=HTMLResponse, dependencies=[Depends(check_csrf)])
async def logo_upload(
    request: Request, file: UploadFile = File(...), o: Owner = Depends(signed_in)
) -> Response:
    from othor.core import logo

    data = await file.read(logo.MAX_BYTES + 1)
    try:
        png = logo.clean(data)
    except logo.LogoRefused as e:
        from othor.web.errors import toast

        return toast(request, str(e), 400)
    from othor.web import tab_icon

    settings = services(request).settings
    sha = logo.store(settings, o.company.id, png)
    tab_icon.store_logo_icons(settings, o.company.id, sha, png)  # the tab's and the phone's
    _set_logo(request, o, sha, "company.logo_set")
    return _logo_row(request, o, "Logo saved", icon_changed=True)


@router.post(
    "/settings/logo/remove", response_class=HTMLResponse, dependencies=[Depends(check_csrf)]
)
def logo_remove(request: Request, o: Owner = Depends(signed_in)) -> Response:
    _set_logo(request, o, None, "company.logo_removed")
    return _logo_row(request, o, "Logo removed", icon_changed=True)


@router.post("/settings/tab-icon", response_class=HTMLResponse, dependencies=[Depends(check_csrf)])
def tab_icon_choose(
    request: Request, choice: str = Form(""), o: Owner = Depends(signed_in)
) -> Response:
    """The browser tab shows the company's letters, or its logo again (Company identity 1.0):
    the owner applies what Settings suggests when the logo won't read at tab size."""
    if choice not in ("letters", "logo"):
        return Response(status_code=400)
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        before = company.tab_icon or "logo"
        if choice != before and company.logo_sha:
            company.tab_icon = "letters" if choice == "letters" else None
            record_event(
                s,
                actor=o.viewer.id,
                action="company.tab_icon_set",
                object_type="company",
                object_id=company.id,
                before={"tab_icon": before},
                after={"tab_icon": choice},
            )
            s.commit()
    toast = "The tab shows the letters" if choice == "letters" else "The tab shows the logo"
    return _logo_row(request, o, toast, icon_changed=True)


# ---------------------------------------------------------------------------------------------
# Your data (v0.18, M18.1): export everything as one zip; a large company's is built by a job

# an expired, missing or another company's export reads the same (9a)
EXPORT_GONE = (
    "This export has expired · build a new one in Settings",
    ("Settings", "/settings?tab=data"),
)


def _export_state(s: Any, svc: Any, o: Owner) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    from othor import exporting

    st = exporting.state(s, svc.settings, o.company.id, svc.clock())
    if st.get("until"):
        st["until_local"] = st["until"].astimezone(ZoneInfo(o.company.timezone))
    return st


def _export_row(request: Request, o: Owner, toast: str = "") -> HTMLResponse:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        st = _export_state(s, svc, o)
    return page(request, "partials/settings_export.html", {"export": st, "toast": toast})


def _zip_response(data: bytes, name: str) -> Response:
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/settings/export")
def export_download(request: Request, o: Owner = Depends(super_admin_only)) -> Response:
    """A small company's export, built while the owner waits."""
    import hashlib

    from othor import exporting
    from othor.core.events import record_event

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if exporting.is_large(s, svc.settings, o.company.id):
            return RedirectResponse("/settings?tab=data", 303)  # the job's flow (the row's button)
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        got = exporting.build(s, svc.settings, company)
        record_event(
            s,
            actor=o.viewer.id,
            action="company.exported",
            object_type="company",
            object_id=o.company.id,
            after={
                "mode": "direct",
                "files": len(got.manifest["files"]),
                "bytes": len(got.data),
                "sha256": hashlib.sha256(got.data).hexdigest(),
            },
        )
        s.commit()
    return _zip_response(got.data, got.filename)


@router.post("/settings/export", response_class=HTMLResponse, dependencies=[Depends(check_csrf)])
def export_request(request: Request, o: Owner = Depends(super_admin_only)) -> Response:
    """A large company's export: a job builds it and emails a link. A second click while one is
    being prepared queues nothing."""
    from othor import exporting
    from othor.core.events import record_event
    from othor.jobs.queue import enqueue

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if exporting.state(s, svc.settings, o.company.id, svc.clock())["status"] != "preparing":
            job = enqueue(s, "export", {})
            record_event(
                s,
                actor=o.viewer.id,
                action="company.export_requested",
                object_type="company",
                object_id=o.company.id,
                after={"job": str(job.id)},
            )
            s.commit()
    return _export_row(request, o, "Building your export · we’ll email you when it’s ready")


@router.get("/settings/export/row", response_class=HTMLResponse)
def export_row(request: Request, o: Owner = Depends(super_admin_only)) -> Response:
    return _export_row(request, o)


@router.get("/settings/export/{job_id}")
def export_file(request: Request, job_id: str, o: Owner = Depends(super_admin_only)) -> Response:
    """A built export: this company's, signed in, less than 48 hours old."""
    import uuid as _uuid

    from othor import exporting
    from othor.core.events import record_event

    svc = services(request)
    try:
        jid = _uuid.UUID(job_id)
    except ValueError:
        return not_found(request, o.site, *EXPORT_GONE)
    with svc.sf() as s, tenant_scope(o.company.id):
        path = exporting.ready(s, svc.settings, o.company.id, jid, svc.clock())
        if path is None:
            return not_found(request, o.site, *EXPORT_GONE)
        record_event(
            s,
            actor=o.viewer.id,
            action="company.export_downloaded",
            object_type="company",
            object_id=o.company.id,
            after={"job": job_id},
        )
        s.commit()
    return _zip_response(path.read_bytes(), f"othor-{o.company.slug}-export.zip")


# ---------------------------------------------------------------------------------------------
# Close the company (v0.18, M18.2): type the name, an emailed code, then Delete


def _close_ctx(svc: Any, company: Company, **kw: Any) -> dict[str, Any]:
    from othor.closing import KEEP_FOR
    from othor.core.localtime import local_date

    return {"step": "name", "purge_on": local_date(company, svc.clock() + KEEP_FOR), **kw}


def _masked(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:1]}•••@{domain}"


def _close_step(request: Request, o: Owner, _status: int = 200, **kw: Any) -> HTMLResponse:
    """One step of the dialog. Always 200: htmx swaps only a 2xx, and the error is shown in
    place (the status the step would have had is kept in the call for reading)."""
    svc = services(request)
    return page(
        request,
        "partials/settings_close_step.html",
        {"cl": _close_ctx(svc, o.company, **kw), "company": o.company},
    )


def _owner_email(request: Request, o: Owner) -> str:
    from othor.mail.delivery import owner

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        who = owner(s)
    return who[0] if who else o.viewer.email


@router.post(
    "/settings/close/start",
    response_class=HTMLResponse,
    dependencies=[Depends(check_csrf), Depends(rate_limit("codes"))],
)
async def close_start(request: Request, o: Owner = Depends(super_admin_only)) -> Response:
    """Step 1: the company's name, typed; then a code to the owner's email."""
    from othor.closing import name_matches
    from othor.core import auth
    from othor.mail.messages import close_code

    svc = services(request)
    form = await request.form()
    resend = form.get("resend") == "1"
    typed = str(form.get("name", ""))
    if not resend and not name_matches(o.company, typed):
        return _close_step(request, o, 400, typed=typed, error="That isn’t the company’s name")
    email = _owner_email(request, o)
    masked = _masked(email)
    try:
        issued = svc.auth.issue_code(email, "close", company_id=o.company.id)
    except auth.ResendTooSoon as e:
        return _close_step(
            request, o, step="code", masked=masked, error=f"Wait {e.seconds_left} s to resend"
        )
    except (auth.Locked, auth.TooManyCodes):
        return _close_step(
            request,
            o,
            429,
            step="code",
            masked=masked,
            state="locked",
            error="Too many codes. Try again in 15 minutes.",
        )
    svc.relay.send(
        close_code(
            to=email,
            code=issued.code,
            company_name=o.company.name,
            slug=o.company.slug,
            settings=svc.settings,
        )
    )
    with svc.sf() as s, tenant_scope(o.company.id):
        record_event(
            s,
            actor=o.viewer.id,
            action="company.close_code_sent",
            object_type="company",
            object_id=o.company.id,
        )
        s.commit()
    return _close_step(request, o, step="code", masked=masked)


@router.post(
    "/settings/close/confirm",
    response_class=HTMLResponse,
    dependencies=[Depends(check_csrf), Depends(rate_limit("codes"))],
)
async def close_confirm(request: Request, o: Owner = Depends(super_admin_only)) -> Response:
    """Step 2: the code, then Delete. The company is closed and the owner lands on the Closed
    page on the root host (the company's own host no longer resolves)."""
    from othor import closing
    from othor.core import auth
    from othor.core.localtime import local_date
    from othor.mail.messages import company_closed
    from othor.mail.relay import MailSendError
    from othor.web.routes.auth import _digits

    svc = services(request)
    code = _digits(await request.form())
    email = _owner_email(request, o)
    masked = _masked(email)
    digits = list(code.ljust(6))[:6]
    try:
        svc.auth.verify_code(email, "close", code, company_id=o.company.id)
    except auth.WrongCode as e:
        left = f"{e.attempts_left} {'try' if e.attempts_left == 1 else 'tries'} left"
        return _close_step(
            request,
            o,
            400,
            step="code",
            masked=masked,
            state="error",
            digits=digits,
            error=f"That code isn’t right · {left}",
        )
    except auth.Locked:
        return _close_step(
            request,
            o,
            429,
            step="code",
            masked=masked,
            state="locked",
            error="Too many wrong codes. Try again in 15 minutes.",
        )
    except (auth.Expired, auth.NoCode):
        return _close_step(
            request,
            o,
            400,
            step="code",
            masked=masked,
            error="That code has expired · send a new one",
        )
    now = svc.clock()
    with svc.sf() as s, tenant_scope(o.company.id):
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        ended = svc.auth.end_company(o.company.id)
        done = closing.close(s, company, actor=str(o.viewer.id), now=now, sessions_ended=ended)
        people = closing.members(s)
        s.commit()
    purge_on = fmt.day_month_year(local_date(o.company, done.purge_after))
    sent = 0
    for to, _name in people:
        try:
            svc.relay.send(
                company_closed(
                    to=to,
                    company_name=o.company.name,
                    slug=o.company.slug,
                    purge_on=purge_on,
                    settings=svc.settings,
                )
            )
            sent += 1
        except MailSendError:
            pass
    with svc.sf() as s, system_scope("closing: the members' email, after the close"):
        from othor.core.events import record_platform_event

        record_platform_event(
            s,
            actor=str(o.viewer.id),
            action="mail.company_closed",
            object_type="company",
            object_id=o.company.id,
            after={"members": len(people), "sent": sent},
        )
        s.commit()
    token = svc.signer("closed").dumps({"name": o.company.name, "purge": purge_on}, now)
    url = svc.settings.instance_url(None, f"/closed?t={token}")
    resp = HTMLResponse("", headers={"HX-Redirect": url})
    resp.delete_cookie(session_cookie_name(request))
    return resp


@router.get("/closed", response_class=HTMLResponse)
def closed_page(request: Request, t: str = "") -> Response:
    """The Closed page, on the root host: what was closed and when the data goes."""
    svc = services(request)
    try:
        got = svc.signer("closed").loads(t, svc.clock(), 24 * 3600)
    except Exception:
        return not_found(request, resolve_site(request))
    return page(request, "pages/closed.html", {"name": got["name"], "purge": got["purge"]})


@router.get("/logo/{sha}.png")
def logo_file(request: Request, sha: str) -> Response:
    from othor.core import logo

    site = resolve_site(request)
    company = site.company
    if company is None or company.logo_sha != sha:
        return Response(status_code=404)
    data = logo.read(services(request).settings, company.id, sha)
    if data is None:
        return Response(status_code=404)
    return Response(
        data,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/tab-icon/{version}-{size}.png")
def tab_icon_file(request: Request, version: str, size: int) -> Response:
    """The company's browser tab icon (16, 32) and home-screen icon (180), on its own host only.
    The version in the URL is what the icon shows (the logo's hash, or the letters and their
    style): a new logo or a rename is a new URL, so each one caches for a year."""
    from othor.web import tab_icon

    company = resolve_site(request).company
    if company is None:
        return Response(status_code=404)
    tag = f'"{version}-{size}"'
    data = tab_icon.read(services(request).settings, company, version, size)
    if data is None:
        return Response(status_code=404)
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "ETag": tag,
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    return Response(data, media_type="image/png", headers=headers)


# ---------------------------------------------------------------------------------------------
# Nav pages that arrive in later milestones: 9a empty states


EMPTY = {
    "home": (
        "Home",
        "Nothing to review yet",
        "Proposals and questions appear after the first run.",
    ),
    "brief": (
        "Morning Brief",
        "No brief yet",
        "The first brief is written after the first nightly run.",
    ),
    "sweep": ("Sweep", "Nothing has fired yet", "Detectors run tonight at 2:00 AM."),
    "ontology": (
        "Business Model",
        "No Business Model yet",
        "It is drafted when setup reaches Metrics.",
    ),
}


# ---------------------------------------------------------------------------------------------
# Account (M15.8, roadmap Q5.5): your name, your sessions on this company, Sign out everywhere
# (the existing POST /logout/everywhere). The device is read from the stored user agent; no IP.


@router.get("/account", response_class=HTMLResponse)
def account_page(
    request: Request,
    saved: int = 0,
    error: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    from othor.web.device import device

    svc = services(request)
    sessions = [
        {
            "device": device(r.user_agent),
            "since": r.created_at,
            "seen": r.last_seen_at,
            "here": r.id == o.session_id,
        }
        for r in svc.auth.live_sessions(o.viewer.id, o.company.id)
    ]
    with svc.sf() as s, tenant_scope(o.company.id):
        user = s.scalars(sa.select(AppUser).where(AppUser.id == o.viewer.id)).one()
        name = user.name or ""
    return page(
        request,
        "pages/account.html",
        {
            **shell_ctx(o, "account"),
            "acct": {
                "name": name,
                "email": o.viewer.email,
                "sessions": sessions,
                "saved": bool(saved),
                "error": "Enter your name." if error == "name" else None,
            },
        },
    )


@router.post("/account/name", dependencies=[Depends(check_csrf)])
async def account_name(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """Your name, as the app and the emails show it: an event with before and after."""
    form = await request.form()
    name = " ".join(str(form.get("name") or "").split())[:200]
    if not name:
        return RedirectResponse("/account?error=name", 303)
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        user = s.scalars(sa.select(AppUser).where(AppUser.id == o.viewer.id)).one()
        before = user.name or ""
        if name != before:
            user.name = name
            record_event(
                s,
                actor=str(o.viewer.id),
                action="user.renamed",
                object_type="user",
                object_id=str(user.id),
                before={"name": before},
                after={"name": name},
            )
            s.commit()
    return RedirectResponse("/account?saved=1", 303)


# ---------------------------------------------------------------------------------------------
# AI usage (M15.8, owner 13–14 Nov 2026; docs/plans/ai-usage.md): counted from the model call
# log by code; tokens only; never a prompt's input or a model's output


def _ai_cap(s: Any, svc: Any, company: Any, tz: Any) -> dict[str, Any] | None:
    """This month's AI cap (open sign-up, owner 16 Nov 2026), or None when there's no cap."""
    from othor.ai import usage

    limit = usage.cap_tokens(company.ai_cap_tokens, svc.settings.ai_monthly_tokens)
    if limit <= 0:
        return None
    now = svc.clock()
    used = usage.used_this_month(s, tz, now)
    return {
        "limit": limit,
        "used": used,
        "capped": used >= limit,
        "resumes": usage.resumes_on(tz, now),
    }


@router.get("/usage/count", response_class=HTMLResponse)
def usage_count(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """The AI usage menu item's count (owner, 18 Nov 2026): this month's tokens, counted from
    the model call log, short ("1.2M")."""
    from zoneinfo import ZoneInfo

    from othor.ai import usage
    from othor.core import fmt

    svc = services(request)
    tz = ZoneInfo(o.company.timezone or "Asia/Kolkata")
    with svc.sf() as s, tenant_scope(o.company.id):
        used = usage.used_this_month(s, tz, svc.clock())
    n = fmt.count_short(used)
    return HTMLResponse(
        f'<span class="nav-count" title="{fmt.num(used)} tokens this month" '
        f'aria-label="{fmt.num(used)} tokens this month">{n}</span>'
    )


@router.post("/contact/send", dependencies=[Depends(check_csrf), Depends(rate_limit("codes"))])
def contact_send(
    request: Request,
    topic: str = Form(""),
    message: str = Form(""),
    back: str = Form("/home"),
    website: str = Form(""),  # the honeypot, as on the website
    o: Owner = Depends(signed_in),
) -> Response:
    """Contact us from the app's side panel (owner, 17 Nov 2026): the owner is known, so the
    topic and the message; stored and emailed to support as the website's form is, then the
    panel says it's sent, in place."""
    from othor.core import contact
    from othor.mail.messages import access_request
    from othor.web.errors import toast
    from othor.web.home_view import trial

    svc = services(request)
    if not contact.available(svc.settings):
        return not_found(request, o.site)
    topic, message = contact.topic(topic), message.strip()
    if not topic:
        return toast(request, "Choose what it’s about", 422)
    if not message and topic not in contact.MESSAGE_OPTIONAL:
        return toast(request, "Write a line about what you need", 422)
    back = local_path(back, "/home")
    name, email = o.viewer.name or o.viewer.email, o.viewer.email
    if not website and svc.auth.contact(
        name, email, o.company.name, message, topic, company_slug=o.company.slug
    ):
        t = trial(o.company, svc.clock(), svc.settings)
        extra = (
            f"Address: {svc.settings.instance_url(o.company.slug, '/')}",
            *([f"Trial: {t['left']} day{'' if t['left'] == 1 else 's'} left"] if t else []),
            "Sent from the app",
        )
        svc.relay.send(
            access_request(
                to=svc.settings.access_request_to,
                name=name,
                email=email,
                company=o.company.name,
                message=message or None,
                settings=svc.settings,
                topic=topic,
                extra=extra,
            )
        )
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}sent=1", 303)


@router.get("/usage", response_class=HTMLResponse)
def usage_page(
    request: Request,
    month: str | None = None,
    task: str | None = None,
    upto: int = 1,
    o: Owner = Depends(signed_in),
) -> Response:
    import datetime as dt
    import re
    from zoneinfo import ZoneInfo

    from othor.ai import usage
    from othor.core.localtime import local_date
    from othor.engine.models import Run

    svc = services(request)
    tz = ZoneInfo(o.company.timezone or "Asia/Kolkata")
    this = local_date(o.company, svc.clock()).replace(day=1)
    if month:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            return not_found(request, o.site)
        picked = dt.date(int(month[:4]), int(month[5:]), 1)
    else:
        picked = this
    upto = min(max(int(upto or 1), 1), 40)
    with svc.sf() as s, tenant_scope(o.company.id):
        months = sorted({this, picked, *usage.months(s, tz)}, reverse=True)
        tasks = usage.tasks(s, picked, tz)
        if task and task not in {t for t, _ in tasks}:
            task = None
        totals = usage.totals(s, picked, tz)
        cap = _ai_cap(s, svc, o.company, tz)
        today = local_date(o.company, svc.clock())
        last30 = usage.daily(s, tz, today, 30)
        rows: list[usage.Row] = []
        more = False
        for p in range(1, upto + 1):
            got, more = usage.log(s, picked, tz, task=task, page=p)
            rows += got
            if not more:
                break
        nxt = (picked.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        nights = int(
            s.scalar(
                sa.select(sa.func.count())
                .select_from(Run)
                .where(Run.kind == "nightly", Run.run_date >= picked, Run.run_date < nxt)
            )
            or 0
        )
    return page(
        request,
        "pages/usage.html",
        {
            **shell_ctx(o, "usage"),
            "u": {
                "month": picked.strftime("%Y-%m"),
                "months": [(m.strftime("%Y-%m"), m.strftime("%B %Y")) for m in months],
                "task": task or "",
                "tasks": [("", "All tasks"), *tasks],
                "totals": totals,
                "cap": cap,
                "support": svc.settings.support_email,
                "form": contact_available(svc.settings),
                "rows": rows,
                "more": more,
                "upto": upto,
                "nights": nights,
                "trend": usage.card_spec(last30),
                "trend_from": last30[0][0],
                "trend_to": last30[-1][0],
                "trend_total": sum(t for _, t in last30),
            },
        },
    )


@router.get("/what-to-do")
def what_to_do_moved(request: Request) -> Response:
    """The page's first address (0.33): it is the Action plan now (owner, 22 Nov 2026)."""
    q = request.url.query
    return RedirectResponse("/action-plan" + (f"?{q}" if q else ""), 301)


@router.get("/action-plan", response_class=HTMLResponse)
def action_plan(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """Action plan (Morning Brief 2.5, #110; named What to do until 22 Nov 2026): today's
    actions across the briefs."""
    from othor.core.localtime import local_date
    from othor.web import todo_view
    from othor.web.routes.sweep import watch_panel

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        p = todo_view.page(s, local_date(o.company, svc.clock()))
        # Watch this in place over the page (0.33.1)
        wp = watch_panel(request, s, o, "action-plan")
        s.expunge_all()
    n = len(p.briefs)
    when = (
        f"From this morning’s {fmt.num(n)} brief{'s' if n != 1 else ''} · "
        f"{fmt.day_month(p.date)}{', ' + p.time if p.time else ''}"
        if p.date and n
        else None
    )
    return page(
        request,
        "pages/what_to_do.html",
        {**shell_ctx(o, "action-plan"), "p": p, "when": when, **(wp or {})},
    )


@router.get("/{area}", response_class=HTMLResponse)
def area(request: Request, area: str, o: Owner = Depends(signed_in)) -> Response:
    if area not in EMPTY:
        return not_found(request, o.site)
    title, empty, text = EMPTY[area]
    state = _state(o.company.id, request)
    resume = (
        None if state is None or state.current_step == "done" else f"/setup/{state.current_step}"
    )
    return page(
        request,
        "pages/area_empty.html",
        {**shell_ctx(o, area), "title": title, "empty": empty, "text": text, "resume": resume},
    )
