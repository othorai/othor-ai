# SPDX-License-Identifier: Apache-2.0
"""Signing in from the root address in a real browser (#14): Continue reaches the company.

The browser enforces the Content-Security-Policy's form-action on the redirect after a form post,
so this can only be tested in a browser. Locally the company host is http, so `form-action 'self'
https:` blocked the redirect and Continue did nothing."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.browser
BASE = "othor.localhost"


@pytest.fixture
def browser() -> Iterator[Any]:
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        yield b
        b.close()


def test_continue_on_the_root_sign_in_goes_to_the_company(
    browser: Any, live: tuple[int, str]
) -> None:
    port, _session = live
    page = browser.new_page()
    blocked: list[str] = []
    page.on("console", lambda m: "Content Security Policy" in m.text and blocked.append(m.text))
    page.goto(f"http://{BASE}:{port}/login", wait_until="networkidle")
    page.fill("input[name=slug]", "indigothread")
    page.click("form.auth-card button[type=submit]")
    page.wait_for_url(f"http://indigothread.{BASE}:{port}/login", timeout=5000)
    assert blocked == []
    page.close()
