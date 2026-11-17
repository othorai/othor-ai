# SPDX-License-Identifier: Apache-2.0
"""The source-host guard (owner, 16 Nov 2026; othor.ai only): with sign-up open, a stranger's
PostgreSQL or MySQL source may not point at our own network. A host that is a sample service name
(`sales-dw`, `erp-db`), a name with no dot, or resolves to any address that isn't public
(private, loopback, link-local and the cloud metadata address, shared CGNAT, multicast, reserved,
IPv6 unique-local or mapped) is refused, at save and again on every connection; the connection
then goes to the address that was checked, so a DNS change between check and connect can't slip
past. Off unless OTHOR_SOURCE_HOST_GUARD is set (self-hosters connect to their own private
databases); OTHOR_SOURCE_HOST_ALLOW names companies (slugs) it skips, the sample company's while
it lives. Never logs or stores the host or an address."""

from __future__ import annotations

import ipaddress
import socket
import uuid
from typing import Any

MESSAGE = (
    "This address is on a private network. "
    "Othor AI connects only to databases reachable from the internet."
)
SAMPLE_NAMES = frozenset({"sales-dw", "erp-db"})


class PrivateHost(Exception):
    """The host is refused (MESSAGE); `kind` is "sample" or "private"."""

    def __init__(self, kind: str) -> None:
        super().__init__(MESSAGE)
        self.kind = kind


class Unresolved(Exception):
    """The host has no address right now: the connection isn't tried (the owner sees "Can't
    reach"), so nothing ever connects to an address that wasn't checked."""


class _Policy:
    def __init__(self) -> None:
        self.on = False
        self.allow: frozenset[str] = frozenset()
        self.sf: Any = None
        self.slugs: dict[uuid.UUID, str] = {}


_policy = _Policy()


def configure(settings: Any, sf: Any = None) -> None:
    """From the settings, once per app or worker (create_app, WorkerContext, EngineContext)."""
    _policy.on = bool(getattr(settings, "source_host_guard", False))
    raw = getattr(settings, "source_host_allow", "") or ""
    _policy.allow = frozenset(x.strip().lower() for x in raw.split(",") if x.strip())
    if sf is not None:
        _policy.sf = sf
    _policy.slugs = {}


def _slug(company_id: uuid.UUID) -> str | None:
    if company_id in _policy.slugs:
        return _policy.slugs[company_id]
    if _policy.sf is None:
        return None
    import sqlalchemy as sa

    from othor.core.models import Company
    from othor.core.tenancy import system_scope

    with _policy.sf() as s, system_scope("source-host guard"):
        slug: str | None = s.scalar(sa.select(Company.slug).where(Company.id == company_id))
    if slug is not None:
        _policy.slugs[company_id] = slug
    return slug


def applies(company_id: uuid.UUID | None = None, slug: str | None = None) -> bool:
    """The guard is on and this company isn't on the allow-list. An unknown company: on."""
    if not _policy.on:
        return False
    if slug is None and company_id is not None:
        slug = _slug(company_id)
    return (slug or "").lower() not in _policy.allow


def _public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def refusal(host: str, port: int) -> tuple[str | None, list[str]]:
    """(why it's refused: "sample" or "private", or None; the checked addresses to connect to).
    A name that doesn't resolve: (None, [])."""
    name = host.strip().rstrip(".").lower()
    if name in SAMPLE_NAMES:
        return "sample", []
    try:
        literal = ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        literal = None
    if literal is None and "." not in name:
        return "private", []  # localhost, a container or service name: never on the internet
    try:
        infos = socket.getaddrinfo(name.strip("[]"), port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return None, []
    addrs = list(dict.fromkeys(str(i[4][0]) for i in infos))
    if not addrs or not all(_public(a) for a in addrs):
        return ("private", []) if addrs else (None, [])
    return None, addrs


def check(host: str, port: int) -> str:
    """For a connection about to be made: the address to connect to, or raise."""
    why, addrs = refusal(host, port)
    if why:
        raise PrivateHost(why)
    if not addrs:
        raise Unresolved(host)
    return addrs[0]
