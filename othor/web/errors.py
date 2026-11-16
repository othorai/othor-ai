# SPDX-License-Identifier: Apache-2.0
"""Error pages (M11a, docs/10 M11 "Error pages"): no request ends on the framework's bare
"Internal Server Error" or "Not Found".

- 404 (9a): says what isn't there and gives one way back. Signed in on the company's own host,
  it renders in the app shell; otherwise bare with the sign-in link. Another company's object
  reads exactly like a missing one.
- 500: "Something went wrong on our side. Nothing you did was lost." + a reference (the
  request id), Reload and Home. Never a trace, SQL or values.
- HTMX requests get 9a's error toast; JSON routes get {"error", "reference"}.
- The metadata DB unreachable or behind: a static page with no database, 503.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Receive, Scope, Send

from othor.core.logging import redact
from othor.ontology.service import MetricCeiling
from othor.web.templating import HERE, templates

log = logging.getLogger("othor.web")

MAINTENANCE = HERE / "static" / "maintenance.html"
ERROR_TEXT = "Something went wrong on our side. Nothing you did was lost."
EXPIRED_TEXT = "This form expired. Reload the page and try again."
JSON_PREFIXES = ("/hooks/", "/healthz")


def exempt(path: str) -> bool:
    """Paths that answer while the DB is behind: health, static files."""
    return path.startswith(("/static/", "/healthz"))


@dataclass(frozen=True)
class Missing:
    """What isn't there, and one way back (9a: "There's no brief for 31 Feb" · Today's brief)."""

    what: str
    back: str
    href: str


NOTHING = Missing("There’s nothing at this address", "Home", "/home")


def request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or "")


def is_htmx(request: Request) -> bool:
    """An htmx request, or an in-place form post from othor.js (M12b): either way a failure
    answers with the 9a toast, not a page."""
    return (
        request.headers.get("hx-request") == "true" or request.headers.get("x-othor-inplace") == "1"
    )


def wants_json(request: Request) -> bool:
    path = request.url.path
    return (
        path.startswith(JSON_PREFIXES)
        or (path.startswith("/sources/") and path.endswith("/picker-token"))
        or path.endswith(".json")
    )


def toast(request: Request, text: str, status: int) -> HTMLResponse:
    """9a's error toast for an HTMX request; othor.js swaps it into the toast dock."""
    resp = templates.TemplateResponse(
        request,
        "partials/toast_error.html",
        {"text": text, "reference": request_id(request)},
        status_code=status,
    )
    resp.headers["HX-Retarget"] = "#toast-dock"
    resp.headers["HX-Reswap"] = "innerHTML"
    resp.headers["X-Othor-Toast"] = "1"
    return resp


def toast_text(request: Request, status: int) -> str:
    area = getattr(request.state, "error_area", None)
    if request.method == "GET":
        return f"Couldn’t load {area}" if area else "Couldn’t load this"
    if status == 404:
        return "Couldn’t save · it isn’t there any more"
    return f"Couldn’t save {area}" if area else "Couldn’t save"


def _owner_ctx(request: Request) -> dict[str, Any] | None:
    """The app shell's context when the viewer is signed in to this company's own host."""
    from othor.web.routes.pages import Bounce, shell_ctx, signed_in

    owner = getattr(request.state, "owner", None)
    if owner is None:
        try:
            owner = signed_in(request)
        except Bounce:
            return None
        except Exception:  # the DB is down: render bare
            return None
    return shell_ctx(owner, "")


def not_found_page(request: Request, missing: Missing | None = None) -> Response:
    if is_htmx(request):
        return toast(request, toast_text(request, 404), 404)
    if wants_json(request):
        return JSONResponse(
            {"error": "not found", "reference": request_id(request)}, status_code=404
        )
    from othor.web.context import resolve_site
    from othor.web.routes.auth import page

    site = getattr(request.state, "site", None)
    if site is None:
        try:
            site = resolve_site(request)
        except Exception:
            site = None
    if site is not None and site.company is not None:
        ctx = _owner_ctx(request)
        if ctx is not None:
            return page(
                request,
                "pages/not_found_app.html",
                {
                    **ctx,
                    "missing": missing
                    or Missing("There’s nothing at this address", "Home", "/home"),
                },
                status=404,
            )
    return page(request, "pages/not_found.html", {"site": site}, status=404)


def _company_id(request: Request) -> uuid.UUID | None:
    site = getattr(request.state, "site", None)
    if site is None:
        from othor.web.context import resolve_site

        try:
            site = resolve_site(request)
        except Exception:  # the DB is the problem: no company to name
            return None
    return site.company.id if site is not None and site.company is not None else None


def _event(request: Request, exc: BaseException) -> None:
    """A `system.error` event on the company (the event table needs one: none on the root host)."""
    cid = _company_id(request)
    if cid is None:
        return
    try:
        from othor.web.context import services
        from othor.web.routes.auth import event

        event(
            services(request),
            cid,
            "system.error",
            object_type="request",
            object_id=uuid.uuid5(uuid.NAMESPACE_URL, request_id(request) or "none"),
            after={
                "reference": request_id(request),
                "method": request.method,
                "path": redact(request.url.path),
                "error": type(exc).__name__,
            },
        )
    except Exception:  # the event is best effort: the page must still render
        log.warning("system.error event not written", extra={"request_id": request_id(request)})


async def server_error(
    scope: Scope, receive: Receive, send: Send, exc: Exception, stats: Any, settings: Any
) -> None:
    request = Request(scope, receive)
    rid = request_id(request)
    cid = _company_id(request)
    extra = {
        "request_id": rid,
        "method": request.method,
        "path": redact(request.url.path),
        "company_id": str(cid) if cid else None,
        "error": type(exc).__name__,
    }
    if settings.env == "dev":
        # the trace goes to the log in dev only; the messages can carry SQL with values
        extra["trace"] = "".join(traceback.format_exception(exc))
    log.error("unhandled error", extra=extra)
    _event(request, exc)
    resp = error_response(request, 500)
    await resp(scope, receive, send)


def error_response(request: Request, status: int) -> Response:
    if is_htmx(request):
        return toast(request, toast_text(request, status), status)
    if wants_json(request):
        return JSONResponse(
            {"error": "server error", "reference": request_id(request)}, status_code=status
        )
    from othor.web.routes.auth import page

    try:
        return page(
            request,
            "pages/error.html",
            {"text": ERROR_TEXT, "reference": request_id(request)},
            status=status,
        )
    except Exception:  # templates themselves failed: plain, still branded
        return HTMLResponse(
            f"<!doctype html><title>Othor AI</title><p>Othor AI</p><p>{ERROR_TEXT}</p>"
            f"<p>Reference {request_id(request)}</p>"
            "<p><a href=''>Reload</a> · <a href='/'>Home</a></p>",
            status_code=status,
        )


async def maintenance(scope: Scope, receive: Receive, send: Send) -> None:
    path: str = scope.get("path", "")
    if path.startswith("/healthz"):
        resp: Response = JSONResponse({"status": "unavailable"}, status_code=503)
    else:
        resp = HTMLResponse(
            MAINTENANCE.read_text(encoding="utf-8"),
            status_code=503,
            headers={"Retry-After": "60", "Cache-Control": "no-store"},
        )
    await resp(scope, receive, send)


def expired(request: Request) -> Response:
    if is_htmx(request):
        return toast(request, EXPIRED_TEXT, 403)
    if wants_json(request):
        return JSONResponse({"error": "expired", "reference": request_id(request)}, status_code=403)
    from othor.web.routes.auth import page

    return page(request, "pages/expired_form.html", {"text": EXPIRED_TEXT}, status=403)


class Limited(Exception):
    """Too many attempts from one network (othor.core.ratelimit)."""


def client_ip(request: Request) -> str:
    from othor.web.context import services

    proxy = services(request).settings.trust_proxy
    if proxy == "fly":
        ip = request.headers.get("fly-client-ip", "").strip()
        if ip:
            return ip
    elif proxy in ("alb", "caddy"):
        # the ALB (and Caddy, M20.4) append the address they saw; earlier hops are whatever the
        # client sent
        ip = request.headers.get("x-forwarded-for", "").rsplit(",", 1)[-1].strip()
        if ip:
            return ip
    return request.client.host if request.client else "unknown"


def rate_limit(which: str) -> Any:
    """A route dependency: count this attempt for the client's network; over the limit →
    the limited page (or toast). `which`: lookup | codes | auth (lookup on the root host,
    codes on a company host)."""
    from othor.core import ratelimit as rl

    def dep(request: Request) -> None:
        from othor.web.context import resolve_site, services

        svc = services(request)
        limit = {"lookup": rl.LOOKUP, "codes": rl.CODES}.get(which)
        if limit is None:
            limit = rl.LOOKUP if resolve_site(request).is_root else rl.CODES
        ok = rl.hit(
            svc.sf,
            limit,
            client_ip(request),
            secret=svc.settings.secret_key.get_secret_value().encode(),
            now=svc.clock(),
        )
        if not ok:
            log.warning(
                "rate limited",
                extra={"request_id": request_id(request), "bucket": limit.bucket},
            )
            cid = _company_id(request)
            if cid is not None:
                from othor.web.routes.auth import event

                event(
                    svc,
                    cid,
                    "auth.rate_limited",
                    object_type="request",
                    object_id=uuid.uuid5(uuid.NAMESPACE_URL, request_id(request) or "none"),
                    after={"bucket": limit.bucket, "path": request.url.path},
                )
            raise Limited()

    return dep


def limited(request: Request) -> Response:
    from othor.core.ratelimit import LIMITED_TEXT

    if is_htmx(request):
        return toast(request, LIMITED_TEXT, 429)
    from othor.web.routes.auth import page

    return page(request, "pages/limited.html", {"text": LIMITED_TEXT}, status=429)


def install(app: Any) -> None:
    @app.exception_handler(Limited)
    async def too_many(request: Request, _exc: Limited) -> Response:
        return limited(request)

    @app.exception_handler(MetricCeiling)
    async def ceiling(request: Request, exc: MetricCeiling) -> Response:
        # the ceiling on metrics (M15.1), from any change that adds one: said plainly
        if is_htmx(request):
            return toast(request, str(exc), 422)
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/ontology?note=ceiling", 303)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404 or exc.status_code == 405:
            return not_found_page(request)
        if exc.status_code == 403:
            return expired(request)
        if exc.status_code >= 500:
            return error_response(request, exc.status_code)
        if is_htmx(request):
            return toast(request, toast_text(request, exc.status_code), exc.status_code)
        if wants_json(request):
            return JSONResponse(
                {"error": "bad request", "reference": request_id(request)},
                status_code=exc.status_code,
            )
        return not_found_page(request)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, _exc: RequestValidationError) -> Response:
        # a bad id or query in the address is a page that isn't there
        if is_htmx(request):
            return toast(request, toast_text(request, 400), 400)
        return not_found_page(request)


__all__ = ["Missing", "install", "not_found_page"]
