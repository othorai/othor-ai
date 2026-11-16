# SPDX-License-Identifier: Apache-2.0
"""Cookies and CSRF.

- Session cookie: HttpOnly, SameSite=Lax, host-only (no Domain), Secure outside dev/test;
  named __Host-othor_session when Secure.
- CSRF: double submit. Every response carries an `othor_csrf` cookie; every POST must send the
  same value as form field `csrf` or header `X-CSRF-Token` (htmx sets it from <body hx-headers>).
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from othor.core.auth import csrf_ok, new_csrf_token
from othor.web.context import services

CSRF_COOKIE = "othor_csrf"


def secure(request: Request) -> bool:
    return services(request).settings.env not in ("dev", "test")


def session_cookie_name(request: Request) -> str:
    return "__Host-othor_session" if secure(request) else "othor_session"


def set_cookie(
    request: Request,
    response: Response,
    name: str,
    value: str,
    max_age: int,
    *,
    http_only: bool = True,
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=http_only,
        samesite="lax",
        secure=secure(request),
        path="/",
    )


def clear_cookie(request: Request, response: Response, name: str) -> None:
    response.delete_cookie(name, path="/", secure=secure(request), samesite="lax")


class CsrfCookieMiddleware(BaseHTTPMiddleware):
    """Make sure each browser has a CSRF token before it renders a form."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        token = request.cookies.get(CSRF_COOKIE) or new_csrf_token()
        request.state.csrf = token
        response = await call_next(request)
        if request.cookies.get(CSRF_COOKIE) != token:
            set_cookie(request, response, CSRF_COOKIE, token, max_age=60 * 60 * 24 * 30)
        return response


async def check_csrf(request: Request) -> None:
    """Dependency for every POST route."""
    submitted = request.headers.get("x-csrf-token")
    if submitted is None:
        form = await request.form()
        value = form.get("csrf")
        submitted = value if isinstance(value, str) else None
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), submitted):
        raise HTTPException(403, "This form expired. Reload the page and try again.")
