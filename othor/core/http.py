# SPDX-License-Identifier: Apache-2.0
"""The one outbound HTTP client (docs/02 "Security"): only allowlisted hosts are reachable.

Google APIs for Sheets, the model provider, and Postmark (the cloud mail relay; on-premise uses
SMTP), plus the hosts a loaded plug-in declares (`Plugin.hosts`). A request to any other host is
refused before it leaves the process.
"""

from __future__ import annotations

import httpx

ALLOWED_HOSTS = frozenset(
    {
        "accounts.google.com",
        "oauth2.googleapis.com",
        "www.googleapis.com",
        "sheets.googleapis.com",
        "api.anthropic.com",
        "api.postmarkapp.com",
    }
)
TIMEOUT_S = 30.0


class HostNotAllowed(RuntimeError):
    pass


def allowed_hosts() -> frozenset[str]:
    """The core's hosts and those the loaded plug-ins declare (`Plugin.hosts`, shown on System
    Health): an enterprise feed's host is reachable only while its plug-in is loaded."""
    from othor.core import plugins

    return ALLOWED_HOSTS | plugins.hosts()


def _check(request: httpx.Request) -> None:
    if request.url.host not in allowed_hosts():
        raise HostNotAllowed(f"outbound host not allowed: {request.url.host}")


def outbound_client(
    transport: httpx.BaseTransport | None = None,
    *,
    also: frozenset[str] = frozenset(),
    timeout: float = TIMEOUT_S,
) -> httpx.Client:
    """`also`: hosts a setting adds for this client only (a monitor's check-in URL, M11b)."""

    def check(request: httpx.Request) -> None:
        if request.url.host not in also:
            _check(request)

    return httpx.Client(
        timeout=timeout,
        event_hooks={"request": [check]},
        transport=transport,
        follow_redirects=False,
    )
