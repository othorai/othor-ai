# SPDX-License-Identifier: Apache-2.0
"""A Sheets client for one source's stored secret, refreshing the access token when needed."""

from __future__ import annotations

import datetime as dt
from typing import Any

from othor.connectors.sheets import oauth
from othor.connectors.sheets.client import (
    FixtureSheetsClient,
    GoogleSheetsClient,
    SheetsClient,
    TokenRevoked,
)
from othor.core.config import Settings


def fixture_folder(settings: Settings) -> Any:
    return settings.sample_dir / "sheets"


def client_for(
    settings: Settings, secret: dict[str, Any] | None
) -> tuple[SheetsClient, dict[str, Any] | None]:
    """Returns the client and, if the token was refreshed, the new secret to store."""
    if settings.sheets_mode == "fixture":
        return FixtureSheetsClient(fixture_folder(settings)), None
    if not secret or not secret.get("refresh_token"):
        raise TokenRevoked("no Google token stored")
    exp = dt.datetime.fromisoformat(secret["expires_at"])
    if exp > dt.datetime.now(dt.UTC):
        return GoogleSheetsClient(secret["access_token"]), None
    try:
        t = oauth.refresh(settings, secret["refresh_token"])
    except Exception as exc:
        raise TokenRevoked("Google access expired or was revoked") from exc
    new = {**secret, **t.secret()}
    return GoogleSheetsClient(t.access_token), new
