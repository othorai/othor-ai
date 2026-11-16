# SPDX-License-Identifier: Apache-2.0
"""The enterprise licence (M20.3, v0.20): signed by Othor AI, checked on the install, offline.

A licence is one line, `othor-licence-v1.<payload>.<signature>` (both base64url): the payload is
JSON naming the customer, the edition, the enterprise features, the limits and the dates; the
signature is Ed25519 over the payload's bytes. The public keys ship here, in the open core; the
private key never leaves Othor AI (tools/licence/, not published). Nothing here touches the
network (rule 7: an on-premise install may block outbound traffic).

States: none · valid · grace (the 14 days after `expires`) · expired · invalid. A feature is
allowed while valid or in grace. Expiry never deletes anything (owner, 16 Nov 2026); what an
expired feature does is decided where it's used (multi-company: no new companies).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

log = logging.getLogger("othor.licence")

PREFIX = "othor-licence-v1"
GRACE_DAYS = 14
# Othor AI's licence-signing public keys, newest first (a rotation adds one at the front).
# The private key never leaves Othor AI (made 16 Nov 2026 with tools/licence/keygen.py).
PUBLIC_KEYS: tuple[str, ...] = ("3-LatNYt9N52Ta2dx3dGJ5nj_JEDp3jjImX3BdpjreU",)


@dataclass(frozen=True)
class Status:
    state: str  # none · valid · grace · expired · invalid
    reason: str = ""
    id: str = ""
    customer: str = ""
    edition: str = ""
    features: frozenset[str] = frozenset()
    limits: dict[str, int] = field(default_factory=dict)
    expires: dt.date | None = None
    days_left: int | None = None  # until expiry (valid)
    grace_left: int | None = None  # days of grace left

    def allows(self, feature: str) -> bool:
        return self.state in ("valid", "grace") and feature in self.features


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_key_text(key: Ed25519PublicKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return _b64(key.public_bytes(Encoding.Raw, PublicFormat.Raw))


def sign(private: Ed25519PrivateKey, payload: dict[str, Any]) -> str:
    """A licence line (tools/licence/sign.py and the tests; the install only verifies)."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"{PREFIX}.{_b64(body)}.{_b64(private.sign(body))}"


def _verified(body: bytes, sig: bytes) -> bool:
    for k in PUBLIC_KEYS:
        try:
            Ed25519PublicKey.from_public_bytes(_unb64(k)).verify(sig, body)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def check(text: str | None, today: dt.date) -> Status:
    """The licence's state on `today`. Reasons name what's wrong, never the key's contents."""
    if not text or not text.strip():
        return Status("none")
    parts = text.strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        return Status("invalid", "not an Othor AI licence")
    try:
        body, sig = _unb64(parts[1]), _unb64(parts[2])
    except ValueError:
        return Status("invalid", "not an Othor AI licence")
    if not _verified(body, sig):
        return Status("invalid", "the signature doesn't match")
    try:
        p = json.loads(body)
        expires = dt.date.fromisoformat(p["expires"]) if p.get("expires") else None
        st = dict(
            id=str(p["id"]),
            customer=str(p["customer"]),
            edition=str(p.get("edition") or "enterprise"),
            features=frozenset(str(f) for f in p.get("features") or []),
            limits={str(k): int(v) for k, v in (p.get("limits") or {}).items()},
            expires=expires,
        )
    except (KeyError, TypeError, ValueError):
        return Status("invalid", "the licence is incomplete")
    if expires is None:
        return Status("valid", **st)  # type: ignore[arg-type]
    if today <= expires:
        return Status("valid", days_left=(expires - today).days, **st)  # type: ignore[arg-type]
    past = (today - expires).days
    if past <= GRACE_DAYS:
        return Status("grace", grace_left=GRACE_DAYS - past, **st)  # type: ignore[arg-type]
    return Status("expired", **st)  # type: ignore[arg-type]


def read(settings: Any) -> str | None:
    """The licence text: OTHOR_LICENCE (a secret store) first, else the file in the data dir."""
    text = str(getattr(settings, "licence", "") or "").strip()
    if text:
        return text
    path = settings.licence_path
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def status(settings: Any, today: dt.date) -> Status:
    return check(read(settings), today)
