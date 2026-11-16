# SPDX-License-Identifier: Apache-2.0
"""A session's device in a few words, from its stored user agent (M15.8, the Account page):
"Chrome on macOS", "Safari on iPhone". Pure, no library; anything unclear is "Unknown device".
The user agent is shown only to its own user; no IP is ever stored (docs/security.md §3)."""

from __future__ import annotations

# order matters: Edge and Opera also say Chrome; Chrome also says Safari
BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("CriOS/", "Chrome"),
    ("Safari/", "Safari"),
)
SYSTEMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("Macintosh", "macOS"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
)


def device(ua: str | None) -> str:
    ua = ua or ""
    browser = next((name for mark, name in BROWSERS if mark in ua), None)
    system = next((name for mark, name in SYSTEMS if mark in ua), None)
    if browser is None or system is None:
        return "Unknown device"
    return f"{browser} on {system}"
