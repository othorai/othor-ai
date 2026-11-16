# SPDX-License-Identifier: Apache-2.0
"""Google OAuth for the `drive.file` scope (docs/05). Tokens are stored as the source secret."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from othor.connectors.sheets.client import SCOPES
from othor.core.config import Settings
from othor.core.http import outbound_client

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint, not a secret


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: dt.datetime

    def secret(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
        }


def authorize_url(settings: Settings, redirect_uri: str, state: str, account: str | None) -> str:
    q = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent select_account",
        "include_granted_scopes": "false",
        "state": state,
    }
    if account:
        q["login_hint"] = account
    return f"{AUTH_URL}?{urlencode(q)}"


def _token(settings: Settings, data: dict[str, str], http: httpx.Client | None) -> Tokens:
    client = http or outbound_client()
    r = client.post(
        TOKEN_URL,
        data={
            **data,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret.get_secret_value(),
        },
    )
    r.raise_for_status()
    d = r.json()
    granted = set(str(d.get("scope", "")).split())
    if granted - set(SCOPES):
        raise ValueError("Google granted more than drive.file; refusing the token")
    return Tokens(
        d["access_token"],
        d.get("refresh_token") or data.get("refresh_token"),
        dt.datetime.now(dt.UTC) + dt.timedelta(seconds=int(d.get("expires_in", 3600)) - 60),
    )


def exchange(
    settings: Settings, code: str, redirect_uri: str, http: httpx.Client | None = None
) -> Tokens:
    return _token(
        settings,
        {"code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
        http,
    )


def refresh(settings: Settings, refresh_token: str, http: httpx.Client | None = None) -> Tokens:
    return _token(settings, {"refresh_token": refresh_token, "grant_type": "refresh_token"}, http)
