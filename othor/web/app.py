# SPDX-License-Identifier: Apache-2.0
"""FastAPI app factory: one app serves the root host (othor.ai) and every company host."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from othor import __version__
from othor.core.auth import AuthService
from othor.core.config import Settings, get_settings
from othor.core.crypto import InstanceKey
from othor.core.db import make_engine, make_session_factory
from othor.core.logging import configure_logging
from othor.core.models import utcnow
from othor.mail.relay import Relay, get_relay
from othor.migrations import runner
from othor.web import errors
from othor.web.context import Services
from othor.web.middleware import Edge, instrument
from othor.web.security import CsrfCookieMiddleware
from othor.web.templating import HERE

log = logging.getLogger("othor.web")
HASHED = re.compile(r"\.[0-9a-f]{8}\.css$")


class HashedStatic(StaticFiles):
    """Static files; a content-hashed build (app.<sha8>.css) is cached for a year."""

    async def get_response(self, path: str, scope: Any) -> Response:
        resp = await super().get_response(path, scope)
        if HASHED.search(path) and resp.status_code == 200:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


class SchemaCheck:
    """Is the metadata DB at the migration head? Checked on start; while it isn't, every page
    is the maintenance page (Edge reads `schema_ok`), re-checked at most every 30 s so a
    deploy-step `othor migrate` clears it without a restart. In `dev` the start migrates."""

    EVERY = 30.0

    def __init__(self, engine: sa.Engine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings
        self.head = runner.head()
        self.ok = True
        self.at: str | None = None
        self.checked = 0.0
        self.lock = threading.Lock()

    @property
    def schema_ok(self) -> bool:
        if not self.ok and time.monotonic() - self.checked > self.EVERY:
            self.check()
        return self.ok

    def check(self) -> bool:
        with self.lock:
            self.checked = time.monotonic()
            try:
                self.at = runner.current(self.engine)
            except sa.exc.DBAPIError:
                return self.ok  # unreachable: Edge serves maintenance per request
            self.ok = self.at == self.head
            return self.ok

    def on_start(self) -> None:
        try:
            at = runner.current(self.engine)
        except sa.exc.DBAPIError:
            log.error("metadata DB unreachable on start")
            return
        if at == self.head:
            self.at, self.ok = at, True
            return
        if self.settings.env == "dev":
            log.warning("metadata DB at %s, head is %s: migrating (dev)", at, self.head)
            runner.upgrade(self.settings.db_url)
            self.check()
            return
        log.error("metadata DB at %s, head is %s: run `othor migrate`", at, self.head)
        self.at, self.ok, self.checked = at, False, time.monotonic()


def create_app(
    settings: Settings | None = None,
    *,
    relay: Relay | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    # plug-ins first (M20.1): the schema check counts their migration branches
    from othor.core import plugins as plugin_reg

    plugin_reg.reset()  # an app reads its own settings' plug-ins (one app per process in prod)
    loaded = plugin_reg.load(settings)
    engine = make_engine(settings.db_url, pool_pre_ping=True)
    instrument(engine)
    sf = make_session_factory(engine)
    key = InstanceKey.load_or_create(
        settings.instance_key_path, allow_create=settings.env != "prod"
    )
    svc = Services(
        settings=settings,
        sf=sf,
        key=key,
        auth=AuthService(sf, key, clock=clock),
        relay=relay or get_relay(settings),
        clock=clock,
    )

    schema = SchemaCheck(engine, settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        schema.on_start()
        yield

    app = FastAPI(
        title="Othor AI",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.services = svc
    app.state.schema = schema
    app.add_middleware(CsrfCookieMiddleware)
    app.add_middleware(Edge, settings=settings, state=schema)  # outermost
    errors.install(app)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> Response:
        """200 when the metadata DB answers and is at the migration head; 503 otherwise."""
        try:
            with engine.connect() as c:
                c.execute(sa.text("SELECT 1"))
        except sa.exc.DBAPIError:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        if not schema.check():
            return JSONResponse({"status": "migrating"}, status_code=503)
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "metadata_db": engine.dialect.name,
                # which image and how many companies (M20.4); no secrets
                "edition": settings.edition,
                "tenancy": settings.tenancy,
            }
        )

    @app.get("/healthz/worker", include_in_schema=False)
    def healthz_worker() -> Response:
        """The worker's heartbeat for monitors (M11b §6): 503 after 10 quiet minutes, or if it
        never beat. Separate from `/healthz`, which the load balancer uses: a stopped worker
        must not take the web out of service."""
        from othor.jobs import heartbeat

        try:
            status, at = heartbeat.state(svc.sf, svc.clock())
        except sa.exc.DBAPIError:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        if at is None:
            return JSONResponse({"status": status}, status_code=503)
        age = int((svc.clock() - at).total_seconds())
        return JSONResponse(
            {"status": status, "heartbeat_age_s": age}, status_code=200 if status == "ok" else 503
        )

    @app.get("/favicon.ico", include_in_schema=False)
    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def brand_icon(request: Request) -> Response:
        """The two icons browsers and phones ask for at the root, on every host (owner, 10 Nov
        2026). Made by scripts/make_brand_images.py; cached for a day."""
        from fastapi.responses import FileResponse

        name = request.url.path.lstrip("/")
        return FileResponse(
            HERE / "static" / name, headers={"Cache-Control": "public, max-age=86400"}
        )

    @app.get("/licences", include_in_schema=False)
    def licences() -> Response:
        """Third-party licences (generated at build time, shipped in the image; 5 Nov 2026)."""
        from fastapi.responses import PlainTextResponse

        for path in (Path("/app/THIRD_PARTY_LICENSES"), HERE.parents[1] / "THIRD_PARTY_LICENSES"):
            if path.is_file():
                return PlainTextResponse(path.read_text())
        return PlainTextResponse(
            "THIRD_PARTY_LICENSES is generated when the image is built (make licences).",
            status_code=404,
        )

    from othor.web.routes import (
        again,
        auth,
        big_picture,
        brief,
        home,
        links,
        meta,
        ontology,
        pages,
        setup,
        sources,
        summary,
        sweep,
    )

    @app.exception_handler(pages.Bounce)
    async def bounce(_request: Request, exc: pages.Bounce) -> Response:
        return exc.response

    # plug-ins (M20.1): their static files under /static/p/<name>/, their templates after the
    # core's, their pages before the core's catch-alls (below)
    from othor.web import templating as tpl

    # the install's outward links (M20.2), for every template
    tpl.templates.env.globals["install"] = {
        "help_url": settings.help_url.rstrip("/"),
        "legal_url": settings.legal_url.rstrip("/"),
        "support_email": settings.support_email,
    }
    tpl.use_plugins([d for p in loaded for d in p.templates])
    for p in loaded:
        for name, folder in p.static:
            at = name if name.startswith("/") else f"/static/p/{name}"
            app.mount(at, HashedStatic(directory=str(folder)), name=f"p-{p.name}-{name}")
    app.mount("/static", HashedStatic(directory=str(HERE / "static")), name="static")
    if settings.env != "prod":
        from othor.web.routes import components

        app.include_router(components.router)
    app.include_router(auth.router)
    app.include_router(meta.router)  # robots.txt and sitemap.xml (M20.2; the website is a plug-in)
    app.include_router(setup.router)  # before pages: /setup/{step} is a catch-all
    app.include_router(sources.router)  # before pages: it ends with a catch-all
    app.include_router(sweep.router)  # before pages: /sweep leaves the 9a empty state
    app.include_router(again.router)  # Run again now (v0.11): before brief's /brief/{bid}
    app.include_router(brief.router)  # before pages: /brief leaves the 9a empty state
    app.include_router(home.router)  # before pages: /home leaves the 9a empty state (M10a)
    app.include_router(summary.router)  # before pages' catch-all /{area} (M16.3)
    app.include_router(big_picture.router)  # before pages' catch-all (v0.19, M19.5)
    app.include_router(ontology.router)  # before pages: /ontology (M10b)
    app.include_router(links.router)  # recipients' signed links (no sign-in) and the mail webhook
    core_paths = {getattr(r, "path", "") for r in app.routes}
    for p in loaded:  # plug-in pages, before pages' catch-alls; a core path can't be taken
        for router in p.routers:
            taken = sorted({getattr(r, "path", "") for r in router.routes} & core_paths)
            if taken:
                plugin_reg.log.error("plug-in %s refused its pages: %s are core", p.name, taken)
                continue
            app.include_router(router)
    app.include_router(pages.router)
    return app
