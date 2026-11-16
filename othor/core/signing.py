# SPDX-License-Identifier: Apache-2.0
"""Small signed, expiring tokens for cookies that carry state between steps (signup, login).

Format: base64url(json) "." base64url(hmac-sha256). Not encrypted: never put secrets in it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime
from typing import Any


class BadSignature(ValueError):
    pass


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Signer:
    def __init__(self, key: bytes, purpose: str) -> None:
        self.key = hashlib.sha256(key + b"|" + purpose.encode()).digest()

    def dumps(self, data: dict[str, Any], now: datetime) -> str:
        body = _b64(
            json.dumps({"d": data, "t": int(now.timestamp())}, separators=(",", ":")).encode()
        )
        sig = _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def loads(self, token: str | None, now: datetime, max_age_s: int) -> dict[str, Any]:
        if not token or "." not in token:
            raise BadSignature("missing")
        body, sig = token.rsplit(".", 1)
        good = _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            raise BadSignature("signature")
        payload = json.loads(_unb64(body))
        if now.timestamp() - payload["t"] > max_age_s:
            raise BadSignature("expired")
        return dict(payload["d"])
