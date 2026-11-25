# SPDX-License-Identifier: Apache-2.0
"""Which model runs a call (Any model, 0.37): the company's own choice for the prompt's kind of
work, then its choice for all kinds, then the install's (env).

`provider_for(settings)` returns a `Router`: it behaves as the install's provider, and the gateway
asks it `for_company(sf, company_id, kind)` on every call, inside that company's tenant scope, so
one company's choice is never used for another. The company's key is decrypted for that call only.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpcore
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.ai.models import ModelSetting
from othor.ai.providers.presets import PRESETS, base_url_of
from othor.core.crypto import DecryptError, InstanceKey, KeyFileError
from othor.core.tenancy import tenant_scope

log = logging.getLogger("othor.ai")
ALL = "all"


def _aad(company_id: uuid.UUID, provider: str) -> bytes:
    return f"model-key:{company_id}:{provider}".encode()


def encrypt_key(key: InstanceKey, company_id: uuid.UUID, provider: str, plain: str) -> bytes:
    return key.encrypt(plain.encode(), aad=_aad(company_id, provider))


def decrypt_key(key: InstanceKey, company_id: uuid.UUID, provider: str, blob: bytes) -> str:
    return key.decrypt(blob, aad=_aad(company_id, provider)).decode()


_KEYS: dict[Path, InstanceKey] = {}


def instance_key(settings: Any) -> InstanceKey | None:
    """The instance key from the data directory (never created here)."""
    path = Path(settings.instance_key_path)
    if path not in _KEYS:
        try:
            _KEYS[path] = InstanceKey.load_or_create(path, allow_create=False)
        except (KeyFileError, OSError):
            return None
    return _KEYS[path]


def rows(s: Session) -> dict[str, ModelSetting]:
    """The company's live choices by kind ("all" and any per-kind), the newest of each.
    Inside a tenant scope."""
    out: dict[str, ModelSetting] = {}
    for r in s.scalars(
        sa.select(ModelSetting)
        .where(ModelSetting.deleted_at.is_(None))
        .order_by(ModelSetting.created_at.desc())
    ):
        out.setdefault(r.kind, r)
    return out


def choice(s: Session, kind: str) -> ModelSetting | None:
    got = rows(s)
    return got.get(kind) or got.get(ALL)


class Unavailable:
    """A company's choice that can't be called right now (its key can't be read, its address is
    refused): callers fall back as for any missing draft."""

    configured = False

    def __init__(self, name: str) -> None:
        self.name = name

    def call(self, *a: Any, **kw: Any) -> None:
        from othor.ai.gateway import ProviderUnavailable

        raise ProviderUnavailable(self.name)


def private_url(company_id: uuid.UUID | None, url: str) -> bool:
    """On othor.ai (the source-host guard on), a model address on a private network is refused,
    as a database's is: a customer's model server must be reachable from the internet."""
    from othor.connectors.sql import hostguard

    if not url or not hostguard.applies(company_id):
        return False
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    why, addrs = hostguard.refusal(host, port)
    return bool(why) or not addrs


def build(
    settings: Any,
    row: ModelSetting,
    company_id: uuid.UUID,
    *,
    key: InstanceKey | None = None,
    transport: Any = None,
) -> Any:
    """The provider for one company's choice."""
    from othor.ai.gateway import AnthropicProvider, OllamaProvider, TemplateProvider
    from othor.ai.providers.openai_compat import OpenAICompatibleProvider

    p = PRESETS.get(row.provider)
    if p is None:
        return Unavailable(row.provider)
    if p.wire == "none":
        return TemplateProvider()
    secret = ""
    if row.key_ciphertext:
        key = key or instance_key(settings)
        if key is None:
            return Unavailable(p.key)
        try:
            secret = decrypt_key(key, company_id, p.key, row.key_ciphertext)
        except DecryptError:
            log.warning("a company's model key could not be read")
            return Unavailable(p.key)
    url = base_url_of(p.key, row.base_url)
    if p.needs_url and private_url(company_id, url):
        return Unavailable(p.key)
    if transport is None and p.needs_url:
        # the owner's own address: checked again at connect, and the connection goes to the
        # address that was checked (as a database source's does)
        transport = guarded_transport(company_id)
    provider: Any
    if p.wire == "anthropic":
        provider = AnthropicProvider(secret, row.model_id)
    elif p.wire == "ollama":
        provider = OllamaProvider(
            url,
            row.model_id,
            context=settings.model_context,
            think=settings.model_think,
            transport=transport,
        )
    else:
        provider = OpenAICompatibleProvider(p, url, secret, row.model_id, transport=transport)
    # the company's own model (its key or its own server), not Othor AI's: outside the monthly
    # AI allowance (owner, 25 Nov 2026); still logged and shown on AI usage
    provider.own = True
    return provider


class GuardedBackend(httpcore.NetworkBackend):
    """httpcore's network backend with the source-host guard at connect time: the host is
    resolved and checked when the connection is opened, and the connection goes to the address
    that was checked, so a DNS change after the check at save can't reach a private address.
    TLS still verifies the name (httpcore passes the URL's host as the server name)."""

    def __init__(self, inner: Any = None) -> None:
        self._inner = inner or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        from othor.connectors.sql import hostguard

        addr = hostguard.check(host, port)  # raises PrivateHost / Unresolved
        stream: httpcore.NetworkStream = self._inner.connect_tcp(
            addr, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )
        return stream

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        raise RuntimeError("unix sockets are not allowed for a model server")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


def guarded_transport(company_id: uuid.UUID, inner: Any = None) -> Any:
    """An HTTP transport whose connections pass the host guard, where it applies (othor.ai);
    None elsewhere (self-hosters reach their own private model servers)."""
    import httpx

    from othor.connectors.sql import hostguard

    if not hostguard.applies(company_id):
        return None
    t = httpx.HTTPTransport()
    t._pool._network_backend = GuardedBackend(inner)
    return t


class Router:
    """What `provider_for` returns: the install's provider, plus each company's own choice."""

    def __init__(self, settings: Any, default: Any, *, transport: Any = None) -> None:
        self.settings = settings
        self.default = default
        self.name = default.name
        self.configured = getattr(default, "configured", False)
        self._transport = transport  # tests: a fake server for every company provider

    def call(self, *a: Any, **kw: Any) -> Any:
        return self.default.call(*a, **kw)

    def for_session(self, s: Session, company_id: uuid.UUID, kind: str) -> Any:
        row = choice(s, kind)
        if row is None:
            return self.default
        return build(self.settings, row, company_id, transport=self._transport)

    def for_company(self, sf: sessionmaker[Session], company_id: uuid.UUID, kind: str) -> Any:
        with sf() as s, tenant_scope(company_id):
            return self.for_session(s, company_id, kind)
