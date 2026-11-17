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
from othor.web.context import Site, Viewer, resolve_site, services
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
SETTINGS_FIELDS = {
    "fiscal_year_start_month": FY_MONTHS,
    "currency": CURRENCIES,
    "timezone": TIMEZONES,
    "report_language": REPORT_LANGUAGES,
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

    fmt.set_request_currency(site.company.currency)  # M15.6: the route formats in it
    with svc.sf() as s:
        user = s.scalars(sa.select(AppUser).where(AppUser.id == sess.user_id)).one()
    with svc.sf() as s, tenant_scope(site.company.id):
        role = s.scalars(sa.select(Membership.role).where(Membership.user_id == user.id)).first()
    if role is None:  # no longer a member of this company
        raise Bounce(not_found(request, site))
    owner = Owner(site, site.company, Viewer.of(user, role), sess.id)
    request.state.owner = owner  # a 404 in this request renders in the app shell (M11a)
    return owner


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


def settings_page(request: Request, o: Owner) -> HTMLResponse:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        floor_v = floor_view(s, svc.settings, company)
        export_v = _export_state(s, svc, o)
        s.expunge_all()
    return page(
        request,
        "pages/settings.html",
        {
            **shell_ctx(o, "settings"),
            "company": company,
            "fy_months": FY_MONTHS,
            "currencies": CURRENCIES,
            "report_languages": REPORT_LANGUAGES,
            "timezones": TIMEZONES,
            "tz_note": TZ_NOTE,
            "sender": f"brief@{svc.settings.mail_from_domain}",
            "floor": floor_v,
            "export": export_v,
            "close": _close_ctx(svc, company),
            "licence": licence_view(svc),
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
    request: Request, key: str = Form(""), o: Owner = Depends(signed_in)
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
def settings_get(request: Request, o: Owner = Depends(signed_in)) -> Response:
    return settings_page(request, o)


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


def floor_view(s: Any, settings: Any, company: Company) -> dict[str, Any]:
    """The floor on Settings (M15.6): the owner's, else the currency's default; and a suggestion,
    1% of a typical month's revenue from the last run, computed by code."""
    from decimal import Decimal

    from othor.briefs import weather
    from othor.engine.models import DETECTING_KINDS, Run
    from othor.engine.store import RunFiles, read_json
    from othor.ontology import service
    from othor.ontology.compiler.build import DEFAULT_FLOOR
    from othor.ontology.models import Spec

    rec = next((r for r in service.active(s, ["materiality"])), None)
    amount = (
        Decimal(rec.body["amount"]) if rec else Decimal(DEFAULT_FLOOR.get(company.currency, "5000"))
    )
    suggest = None
    run = s.scalars(
        sa.select(Run)
        .where(Run.status.in_(("done", "partial")), Run.kind.in_(DETECTING_KINDS))
        .order_by(Run.number.desc())
        .limit(1)
    ).first()
    if run is not None:
        spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
        base = read_json(RunFiles(settings.runs_dir(company.id), run.id).baselines) or {}
        got = weather.revenue(dict(spec.compiled), base)
        if got is not None and got[1] > 0:
            suggest = (got[1] / 100).quantize(Decimal("1000"))
    return {"amount": amount, "owner": rec is not None, "suggest": suggest or None}


@router.post("/settings", response_class=HTMLResponse, dependencies=[Depends(check_csrf)])
def settings_post(
    request: Request, field: str = Form(""), value: str = Form(""), o: Owner = Depends(signed_in)
) -> Response:
    if field == "materiality":  # the floor the owner sets (M15.6): an Ontology version
        return _save_floor(request, o, value)
    if field in SETTINGS_FIELDS:
        if value not in {o[0] for o in SETTINGS_FIELDS[field]}:
            return HTMLResponse("Not a valid choice.", status_code=400)
        parsed: Any = int(value) if field == "fiscal_year_start_month" else value
    elif field in ("email_delivery", "weekly_digest"):
        parsed = value in ("on", "true", "1")
    else:
        return _plugin_setting(request, o, field, value)
    ev = _apply(request, o, {field: parsed}, "settings.changed")
    if ev is not None and field in SPEC_FIELDS:
        _settings_version(request, o, field, parsed)
    if field in ("email_delivery", "weekly_digest"):
        # a switch shows its own state; flipping it back undoes it, so no "saved · Undo" toast
        # (#17). The change is an event all the same.
        return Response(status_code=204)
    label = {
        "fiscal_year_start_month": "Fiscal year start",
        "currency": "Currency",
        "timezone": "Time zone",
        "report_language": "Big Picture language",
        "email_delivery": "Email delivery",
        "weekly_digest": "Weekly digest",
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


def _save_floor(request: Request, o: Owner, value: str) -> Response:
    from decimal import Decimal, InvalidOperation

    from othor.ontology import service
    from othor.ontology.compiler import compile_version
    from othor.ontology.types import validate_body

    try:
        amount = Decimal(value.replace(",", "").strip())
        body = validate_body("materiality", {"amount": f"{amount:f}", "source": "owner"})
    except (InvalidOperation, ValueError):
        return HTMLResponse("Enter an amount above zero.", status_code=400)
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        v = service.apply(
            s,
            [service.put("materiality", "company", body)],
            actor=str(o.viewer.id),
            reason="Settings: the floor",
        )
        if v is not None and service.confirmed(s):
            compile_version(s, company, actor=str(o.viewer.id))
        s.commit()
    return page(
        request,
        "partials/toast_saved.html",
        {"text": "Floor saved · applies from tonight · Undo in Ontology History", "event_id": None},
    )


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


def _logo_row(request: Request, o: Owner, toast: str | None = None) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        company = Repo(s).get(Company, o.company.id)
        assert company is not None
        s.expunge_all()
    return page(request, "partials/settings_logo.html", {"company": company, "toast": toast})


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
    sha = logo.store(services(request).settings, o.company.id, png)
    _set_logo(request, o, sha, "company.logo_set")
    return _logo_row(request, o, "Logo saved")


@router.post(
    "/settings/logo/remove", response_class=HTMLResponse, dependencies=[Depends(check_csrf)]
)
def logo_remove(request: Request, o: Owner = Depends(signed_in)) -> Response:
    _set_logo(request, o, None, "company.logo_removed")
    return _logo_row(request, o, "Logo removed")


# ---------------------------------------------------------------------------------------------
# Your data (v0.18, M18.1): export everything as one zip; a large company's is built by a job

# an expired, missing or another company's export reads the same (9a)
EXPORT_GONE = ("This export has expired · build a new one in Settings", ("Settings", "/settings"))


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
def export_download(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """A small company's export, built while the owner waits."""
    import hashlib

    from othor import exporting
    from othor.core.events import record_event

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if exporting.is_large(s, svc.settings, o.company.id):
            return RedirectResponse("/settings", 303)  # the job's flow (the row's button)
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
def export_request(request: Request, o: Owner = Depends(signed_in)) -> Response:
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
def export_row(request: Request, o: Owner = Depends(signed_in)) -> Response:
    return _export_row(request, o)


@router.get("/settings/export/{job_id}")
def export_file(request: Request, job_id: str, o: Owner = Depends(signed_in)) -> Response:
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
async def close_start(request: Request, o: Owner = Depends(signed_in)) -> Response:
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
async def close_confirm(request: Request, o: Owner = Depends(signed_in)) -> Response:
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
    "ontology": ("Ontology", "No Ontology yet", "It is drafted when setup reaches Metrics."),
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
                actor=o.viewer.email,
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


@router.get("/usage/card", response_class=HTMLResponse)
def usage_card(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """The side menu's AI usage card (owner, 14 Nov 2026): tokens in the last 7 days and a bar
    a day, counted from the log; loaded after the page so no page waits for it."""
    from zoneinfo import ZoneInfo

    from othor.ai import usage
    from othor.core import fmt
    from othor.core.localtime import local_date

    svc = services(request)
    tz = ZoneInfo(o.company.timezone or "Asia/Kolkata")
    with svc.sf() as s, tenant_scope(o.company.id):
        days = usage.daily(s, tz, local_date(o.company, svc.clock()), 7)
        cap = _ai_cap(s, svc, o.company, tz)
    spec = usage.card_spec(days)
    card = {
        "total": fmt.count_short(sum(t for _, t in days)),
        "alt": spec.alt_text,
        "spec": spec,
        "cap": cap,
    }
    return page(request, "partials/usage_card.html", {"c": card})


@router.post("/contact/send", dependencies=[Depends(check_csrf), Depends(rate_limit("codes"))])
def contact_send(
    request: Request,
    topic: str = Form("other"),
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
    if not message and topic not in contact.MESSAGE_OPTIONAL:
        return toast(request, "Write a line about what you need", 422)
    if not back.startswith("/") or back.startswith("//"):
        back = "/home"
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
