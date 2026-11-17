# SPDX-License-Identifier: Apache-2.0
"""One check for redirect targets (CodeQL py/url-redirection, 17 Nov 2026): only a path on this
site, or an absolute URL on this host turned into its path."""

from __future__ import annotations

import pytest

from othor.web.context import local_path
from othor.web.routes.brief import _plain_email

HOST = "indigo.othor.ai"


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("/home?panel=contact", "/home?panel=contact"),
        ("/sources", "/sources"),
        (f"https://{HOST}/sweep?run=3", "/sweep?run=3"),
        ("https://evil.example/home", "/x"),
        ("//evil.example/home", "/x"),
        ("/\\evil.example", "/x"),
        ("javascript:alert(1)", "/x"),
        ("home", "/x"),
        ("/home\r\nSet-Cookie: a=b", "/x"),
        ("", "/x"),
        (None, "/x"),
        (f"https://{HOST}.evil.example/", "/x"),
    ],
)
def test_only_paths_on_this_site(raw: str | None, want: str) -> None:
    assert local_path(raw, "/x", HOST) == want


def test_without_a_host_absolute_urls_are_refused() -> None:
    assert local_path(f"https://{HOST}/home", "/x") == "/x"


@pytest.mark.parametrize(
    ("email", "ok"),
    [
        ("priya@tessora.example", True),
        ("a@b.co", True),
        ("no-at.example", False),
        ("two@@b.example", False),
        ("sp ace@b.example", False),
        ("x@nodot", False),
        ("!@!." + "!@!." * 5000, False),
        ("a" * 330 + "@b.example", False),
    ],
)
def test_recipient_email_check(email: str, ok: bool) -> None:
    assert _plain_email(email) is ok
