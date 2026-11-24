# SPDX-License-Identifier: Apache-2.0
"""Per-app services and per-request site resolution (root host vs <slug>.<base>)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from othor.core.auth import AuthService
from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.models import AppUser, Company, utcnow
from othor.core.signing import Signer
from othor.mail.relay import Relay

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$")
RESERVED = {
    "www",
    "app",
    "api",
    "admin",
    "static",
    "mail",
    "help",
    "support",
    "login",
    "signup",
    "othor",
    "status",
    "docs",
    "ips",
    "auth",
    "root",
    "dev",
    "staging",
    "blog",  # blog.othor.ai: the old blog's address, redirected by the website (23 Nov 2026)
}


@dataclass
class Services:
    settings: Settings
    sf: sessionmaker[Session]
    key: InstanceKey
    auth: AuthService
    relay: Relay
    clock: Callable[[], datetime] = utcnow

    def signer(self, purpose: str) -> Signer:
        return Signer(self.key.subkey(uuid.UUID(int=0), "cookies"), purpose)


@dataclass(frozen=True)
class Site:
    kind: str  # root | company | unknown
    host: str  # without port
    slug: str | None
    company: Company | None

    @property
    def is_root(self) -> bool:
        return self.kind == "root"


def services(request: Request) -> Services:
    svc: Services = request.app.state.services
    return svc


def resolve_site(request: Request) -> Site:
    from othor.core import fmt

    cached = getattr(request.state, "site", None)
    if isinstance(cached, Site):
        site = cached
    else:
        site = _resolve_site(request)
        request.state.site = site  # the error pages and logs read it (M11a)
    if site.company is not None:  # every company page, signed in or a recipient's link (M15.6)
        fmt.set_request_currency(site.company.currency)
        fmt.set_request_chart(site.company)  # v0.25: smooth lines, values on lines
    return site


def _resolve_site(request: Request) -> Site:
    svc = services(request)
    base = svc.settings.base_domain
    host = (request.headers.get("host") or "").split(":")[0].lower().rstrip(".")
    if svc.settings.tenancy == "single":  # one company per install (M20.1): every host is it
        with svc.sf() as s:
            company = s.scalars(
                sa.select(Company).where(Company.deleted_at.is_(None)).order_by(Company.created_at)
            ).first()
        if company is None:
            return Site("root", host, None, None)  # not set up yet: sign-up
        return Site("company", host, company.slug, company)
    if host in (base, "localhost", "127.0.0.1", "testserver") or not host.endswith("." + base):
        return Site("root", host, None, None)
    slug = host[: -len(base) - 1]
    with svc.sf() as s:
        company = s.scalars(
            sa.select(Company).where(Company.slug == slug, Company.deleted_at.is_(None))
        ).first()
    if company is None:
        return Site("unknown", host, slug, None)
    return Site("company", host, slug, company)


def company_url(request: Request, slug: str, path: str = "/") -> str:
    svc = services(request)
    if svc.settings.tenancy == "single":  # one company per install: its own host, no slug
        return str(request.base_url).rstrip("/") + path
    port = request.url.port
    default = (request.url.scheme == "http" and port == 80) or (
        request.url.scheme == "https" and port == 443
    )
    netloc = f"{slug}.{svc.settings.base_domain}" + (f":{port}" if port and not default else "")
    return f"{request.url.scheme}://{netloc}{path}"


def root_url(request: Request, path: str = "/") -> str:
    svc = services(request)
    if svc.settings.tenancy == "single":
        return str(request.base_url).rstrip("/") + path
    port = request.url.port
    default = (request.url.scheme == "http" and port == 80) or (
        request.url.scheme == "https" and port == 443
    )
    netloc = svc.settings.base_domain + (f":{port}" if port and not default else "")
    return f"{request.url.scheme}://{netloc}{path}"


def initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


@dataclass(frozen=True)
class Viewer:
    """The signed-in owner, as templates see them."""

    id: uuid.UUID
    name: str
    email: str
    role: str = "owner"

    @property
    def initials(self) -> str:
        return initials(self.name or self.email)

    @property
    def role_label(self) -> str:
        return self.role.capitalize()

    @classmethod
    def of(cls, user: AppUser, role: str = "owner") -> Viewer:
        return cls(user.id, user.name or user.email.split("@")[0], user.email, role)


def local_path(raw: str | None, default: str, host: str | None = None) -> str:
    """A redirect target that stays on this site: a path (and query), or an absolute URL on
    `host` turned into its path; anything else, `default`. One check for every "back", "next"
    and HX-Current-URL redirect (CodeQL py/url-redirection, 17 Nov 2026)."""
    from urllib.parse import urlsplit

    if not raw or "\\" in raw or any(ord(ch) < 32 for ch in raw):
        return default
    parts = urlsplit(raw)
    off_site = host is None or parts.netloc != host or parts.scheme not in ("http", "https")
    if (parts.scheme or parts.netloc) and off_site:
        return default
    path = parts.path or "/"
    if not path.startswith("/") or path.startswith("//"):
        return default
    return path + (f"?{parts.query}" if parts.query else "")
