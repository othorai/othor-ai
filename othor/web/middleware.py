# SPDX-License-Identifier: Apache-2.0
"""The edge of every request (M11a), one pure-ASGI middleware, outermost:

- a request id (`X-Request-ID`, echoed; in every log line through a context variable);
- `Server-Timing` (app and metadata-DB time, query count) and one log line per request;
- security headers (CSP, nosniff, referrer, frame-ancestors; HSTS outside dev);
- maintenance: while the metadata DB is behind the migration head, or when it can't be
  reached, a static page that needs no database (503);
- any uncaught exception: the 500 page with the request id as its reference. The exception
  is handled here and not re-raised, so neither the server nor the framework prints a trace
  with values in it (`dev` logs the trace).
"""

from __future__ import annotations

import contextvars
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from othor.web import seo

log = logging.getLogger("othor.web")

REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


@dataclass
class RequestStats:
    request_id: str = ""
    db_ms: float = 0.0
    db_queries: int = 0
    company_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


CURRENT: contextvars.ContextVar[RequestStats | None] = contextvars.ContextVar(
    "othor_request", default=None
)


def current_request_id() -> str | None:
    st = CURRENT.get()
    return st.request_id if st else None


def instrument(engine: sa.Engine) -> None:
    """Count metadata-DB queries and their time into the current request's stats."""

    @sa.event.listens_for(engine, "before_cursor_execute")
    def _before(conn: Any, *_a: Any) -> None:
        conn.info.setdefault("othor_t0", []).append(time.perf_counter())

    @sa.event.listens_for(engine, "after_cursor_execute")
    def _after(conn: Any, *_a: Any) -> None:
        stack = conn.info.get("othor_t0")
        t0 = stack.pop() if stack else None
        st = CURRENT.get()
        if st is not None and t0 is not None:
            st.db_ms += (time.perf_counter() - t0) * 1000
            st.db_queries += 1


SCRIPT_HOSTS = "https://apis.google.com"  # the Google Picker only (docs/05)
# where the Picker is shown: Sources, and setup's Sources step (11 Nov 2026: it was blocked there)
PICKER_PATHS = ("/sources", "/setup/sources")


VIDEO_FRAMES = (
    "https://www.youtube-nocookie.com"  # a blog post's video, after a click (docs/security)
)
# where a video may play: the website's blog posts only (owner, 23 Nov 2026); nothing loads from
# YouTube until the reader presses play, and no YouTube script runs on the page itself
VIDEO_PATHS = ("/blog/",)


def security_headers(path: str, env: str, base_domain: str = "") -> list[tuple[bytes, bytes]]:
    picker = path.startswith(PICKER_PATHS)
    script = "'self'" + (f" {SCRIPT_HOSTS}" if picker else "")
    frame = (
        "https://docs.google.com"
        if picker
        else VIDEO_FRAMES
        if path.startswith(VIDEO_PATHS)
        else "'none'"
    )
    # Signing in from the root address posts there and is redirected to the company's own host
    # (1a → 1b, and signup's handoff); form-action covers that redirect. Without a scheme these
    # match the page's own: http locally (where https: alone blocked it, #14), https live.
    company_hosts = f" {base_domain}:* *.{base_domain}:*" if base_domain else ""
    csp = (
        "default-src 'self'; "
        f"script-src {script}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'" + (f" {SCRIPT_HOSTS}" if picker else "") + "; "
        f"frame-src {frame}; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self' https:" + company_hosts
    )
    out = [
        (b"content-security-policy", csp.encode()),
        (b"x-content-type-options", b"nosniff"),
        # the Picker pages send their origin (only) to Google, which checks the API key's
        # website restriction against it (11 Nov 2026); every other page sends nothing
        (b"referrer-policy", b"strict-origin" if picker else b"same-origin"),
    ]
    if env not in ("dev", "test"):
        out.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
    return out


def is_db_down(exc: BaseException) -> bool:
    """A metadata-DB connection failure (not a bad query)."""
    if isinstance(exc, sa.exc.DBAPIError) and exc.connection_invalidated:
        return True
    if isinstance(exc, sa.exc.OperationalError):
        text = str(exc.orig).lower() if exc.orig else ""
        return any(
            k in text
            for k in (
                "connection",
                "could not connect",
                "unable to open database",
                "server closed",
                "timeout",
                "can't connect",
                "lost connection",
                "no such table",
            )
        )
    return False


def _host_redirect(
    scope: Scope, host: str, headers: dict[bytes, bytes], settings: Any
) -> str | None:
    """A 301's target when `<label>.<base>` is a sub-domain a plug-in answers
    (Plugin.host_redirects, 23 Nov 2026), else None. The target is always on the root host:
    the plug-in gives the path."""
    base = str(getattr(settings, "base_domain", "") or "")
    if not base or getattr(settings, "tenancy", "") == "single" or not host.endswith("." + base):
        return None
    from othor.core import plugins

    fn = plugins.host_redirect(host[: -len(base) - 1])
    if fn is None:
        return None
    query = (scope.get("query_string") or b"").decode("latin-1")
    to = fn(scope.get("path", "") or "/", query)
    if not to.startswith("/") or to.startswith("//"):
        to = "/"
    raw = headers.get(b"host", b"").decode("latin-1")
    port = raw.rsplit(":", 1)[1] if ":" in raw else ""
    netloc = base + (f":{port}" if port.isdigit() and port not in ("80", "443") else "")
    return f"{scope.get('scheme', 'https')}://{netloc}{to}"


class Edge:
    def __init__(self, app: ASGIApp, *, settings: Any, state: Any) -> None:
        self.app = app
        self.settings = settings
        self.state = state  # the FastAPI app.state: schema_ok

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from othor.web import errors

        headers = dict(scope.get("headers") or [])
        if self.settings.trust_proxy in ("alb", "caddy"):
            # the ALB (or Caddy, self-hosted) ends TLS; links follow what the client used
            proto = headers.get(b"x-forwarded-proto", b"").decode("latin-1").strip().lower()
            if proto in ("http", "https"):
                scope["scheme"] = proto
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if REQUEST_ID.match(incoming) else secrets.token_hex(6)
        stats = RequestStats(request_id=rid)
        token = CURRENT.set(stats)
        from othor.core import fmt

        money_token = fmt.request_holder()  # the company's currency, charts, zone, once known
        scope.setdefault("state", {})["request_id"] = rid
        path: str = scope.get("path", "")
        host = headers.get(b"host", b"").decode("latin-1").split(":")[0].lower().rstrip(".")
        t0 = time.perf_counter()
        started = False
        status = 500

        async def send_wrapped(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                hs = list(message.get("headers", []))
                names = {k.lower() for k, _ in hs}
                hs.append((b"x-request-id", rid.encode()))
                total = (time.perf_counter() - t0) * 1000
                timing = (
                    f"app;dur={total:.1f}, "
                    f'db;dur={stats.db_ms:.1f};desc="{stats.db_queries} queries"'
                )
                hs.append((b"server-timing", timing.encode()))
                for k, v in security_headers(path, self.settings.env, self.settings.base_domain):
                    if k not in names:
                        hs.append((k, v))
                if b"x-robots-tag" not in names and seo.noindex(
                    host,
                    path,
                    base_domain=self.settings.base_domain,
                    public_site=self.settings.public_site,
                ):
                    hs.append((b"x-robots-tag", seo.NOINDEX))
                message = {**message, "headers": hs}
            await send(message)

        try:
            moved = _host_redirect(scope, host, headers, self.settings)
            if moved is not None:  # a plug-in's own sub-domain (blog.othor.ai): never a company
                await send_wrapped(
                    {
                        "type": "http.response.start",
                        "status": 301,
                        "headers": [
                            (b"location", moved.encode()),
                            (b"content-length", b"0"),
                            (b"cache-control", b"public, max-age=86400"),
                        ],
                    }
                )
                await send_wrapped({"type": "http.response.body", "body": b""})
                return
            if not getattr(self.state, "schema_ok", True) and not errors.exempt(path):
                await errors.maintenance(scope, receive, send_wrapped)
                return
            try:
                await self.app(scope, receive, send_wrapped)
            except Exception as exc:  # the last stop: never the framework's page or a trace
                if started:
                    log.error(
                        "response failed after it started",
                        extra={"request_id": rid, "path": path, "error": type(exc).__name__},
                    )
                    return
                if is_db_down(exc):
                    log.error(
                        "metadata DB unreachable",
                        extra={"request_id": rid, "path": path, "error": type(exc).__name__},
                    )
                    await errors.maintenance(scope, receive, send_wrapped)
                    return
                await errors.server_error(scope, receive, send_wrapped, exc, stats, self.settings)
        finally:
            took = (time.perf_counter() - t0) * 1000
            if not path.startswith("/static"):
                log.info(
                    "request",
                    extra={
                        "request_id": rid,
                        "method": scope.get("method"),
                        "path": path,
                        "status": status,
                        "dur_ms": round(took, 1),
                        "db_ms": round(stats.db_ms, 1),
                        "db_queries": stats.db_queries,
                    },
                )
            CURRENT.reset(token)
            fmt.end_request(money_token)
