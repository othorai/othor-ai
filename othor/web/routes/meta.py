# SPDX-License-Identifier: Apache-2.0
"""Files for crawlers on every host (M20.2: core, so an instance without the website still
answers): robots.txt and sitemap.xml, from the indexable pages a website plug-in offers."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from othor.web import seo
from othor.web.context import Site, root_url, services
from othor.web.routes.auth import not_found

router = APIRouter()


@router.get("/robots.txt")
def robots_text(request: Request) -> Response:
    """On every host, with no database: the website for search on the root host; nothing on a
    company host, or on an instance without the public site (on-premise)."""
    svc = services(request)
    host = (request.headers.get("host") or "").split(":")[0].lower().rstrip(".")
    body = seo.robots_txt(
        on_root=host == svc.settings.base_domain,
        public_site=svc.settings.public_site,
        sitemap_url=root_url(request, "/sitemap.xml"),
    )
    return PlainTextResponse(body)


@router.get("/sitemap.xml")
def sitemap(request: Request) -> Response:
    """The website's pages (a plug-in's indexable list); root host only, and only with one."""
    svc = services(request)
    host = (request.headers.get("host") or "").split(":")[0].lower().rstrip(".")
    if (
        not svc.settings.public_site
        or host != svc.settings.base_domain
        or not seo.indexable_paths()
    ):
        return not_found(request, Site("root", host, None, None))
    body = seo.sitemap_xml(root_url(request, "").rstrip("/"))
    return Response(body, media_type="application/xml")
