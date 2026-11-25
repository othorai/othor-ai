# SPDX-License-Identifier: Apache-2.0
"""#115: the company address is lower-cased as it is typed (the hint says lowercase only), and
the server saves it lower-cased whatever arrives."""

from __future__ import annotations

import re
from pathlib import Path

from tests.web.conftest import ROOT, Env

JS = Path(__file__).parents[2] / "othor" / "web" / "static" / "js" / "othor.js"


def _to_address(env: Env) -> str:
    c = env.client
    tok = env.csrf(f"{ROOT}/signup")
    c.post(
        f"{ROOT}/signup", data={"csrf": tok, "name": "A", "email": "a@acme.in", "company": "Acme"}
    )
    c.post(f"{ROOT}/signup/verify", data={"csrf": tok, "code": env.last_code()})
    return tok


def test_the_address_field_lowercases_as_typed(env: Env) -> None:
    _to_address(env)
    page = env.client.get(f"{ROOT}/signup/address").text
    field = re.search(r'<input id="f-slug"[^>]*>', page, re.S)
    assert field and "data-lowercase" in field.group(0)
    assert "Lowercase letters, numbers and hyphens." in page
    login = env.client.get(f"{ROOT}/login").text
    field = re.search(r'<input id="f-slug"[^>]*>', login, re.S)
    assert field and "data-lowercase" in field.group(0)
    js = JS.read_text()
    assert "[data-lowercase]" in js and "toLowerCase()" in js


def test_the_server_saves_the_address_lowercased(env: Env) -> None:
    tok = _to_address(env)
    check = env.client.get(f"{ROOT}/signup/address/check", params={"slug": "AcmeCo"}).text
    assert "acmeco.othor.localhost is available" in check
    r = env.client.post(
        f"{ROOT}/signup/address",
        data={
            "csrf": tok,
            "slug": " AcmeCo ",
            "website": "",
            "fy": "4",
            "currency": "INR",
            "tz": "Asia/Kolkata",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith(env.host("acmeco"))
