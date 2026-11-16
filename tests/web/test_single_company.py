# SPDX-License-Identifier: Apache-2.0
"""One company per install (M20.1, owner 16 Nov 2026): the open core runs one company on one
domain; sign-up sets it up once, then closes; links carry no subdomain."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from othor.core.models import Company
from tests.web.conftest import make_env

HOST = "http://reports.example.org"


def _signup(env, company="Indigo Thread Co."):  # type: ignore[no-untyped-def]
    c = env.client
    tok = env.csrf(f"{HOST}/signup")
    r = c.post(
        f"{HOST}/signup",
        data={
            "csrf": tok,
            "name": "Uday Kamath",
            "email": "uk@indigothread.example",
            "company": company,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    code = env.last_code("uk@indigothread.example")
    r = c.post(f"{HOST}/signup/verify", data={"csrf": tok, "code": code}, follow_redirects=False)
    assert r.headers["location"] == "/signup/address", r.text
    page = c.get(f"{HOST}/signup/address").text
    assert '<input type="hidden" name="slug" value="indigo">' in page
    assert "Company address" not in page
    r = c.post(
        f"{HOST}/signup/address",
        data={
            "csrf": tok,
            "slug": "indigothread",
            "website": "",
            "fy": "4",
            "currency": "INR",
            "tz": "Asia/Kolkata",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    return r


def test_one_company_is_set_up_once_on_its_own_host(tmp_path: Path) -> None:
    with make_env(
        tmp_path, tenancy="single", signup="open", base_domain="reports.example.org"
    ) as env:
        # before setup, the address goes straight to "Set up your company"
        r = env.client.get(f"{HOST}/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/signup"
        r = _signup(env)
        # the handoff stays on the install's own host: no subdomain
        assert r.headers["location"].startswith(f"{HOST}/auth/handoff?t=")
        env.client.get(r.headers["location"])
        home = env.client.get(f"{HOST}/home")
        assert home.status_code == 200 and "Indigo Thread Co." in home.text
        # any host name the install answers on is the company
        assert "Indigo Thread Co." in env.client.get("http://10.0.0.5/home").text
        # sign-up is closed now
        again = env.client.get(f"{HOST}/signup")
        assert again.status_code == 409 and "is already set up" in again.text
        with env.app.state.services.sf() as s:  # type: ignore[attr-defined]
            assert s.scalar(sa.select(sa.func.count()).select_from(Company)) == 1
        # links in emails carry no subdomain
        settings = env.app.state.services.settings  # type: ignore[attr-defined]
        assert settings.instance_url("indigo", "/brief").endswith("://reports.example.org/brief")


def test_multi_keeps_subdomains(tmp_path: Path) -> None:
    with make_env(tmp_path) as env:
        settings = env.app.state.services.settings  # type: ignore[attr-defined]
        assert settings.tenancy == "multi"
        url = settings.instance_url("indigothread", "/brief")
        assert url.split("//")[1].startswith("indigothread.")
