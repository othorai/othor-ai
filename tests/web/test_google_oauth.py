# SPDX-License-Identifier: Apache-2.0
"""Google sign-in for Sheets through one redirect URI (M11b). Google allows no wildcard redirect
URIs, so every company's sign-in comes back to the root host; the signed `state` says which
company it was, and the root host hands the owner back to that company's host to finish."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from othor.connectors.sheets.oauth import Tokens
from tests.web.conftest import ROOT, Env, signup
from tests.web.test_sources_web import _to_sources

CALLBACK = f"{ROOT}/sources/google/callback"


@pytest.fixture
def google(env: Env, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Real mode, with Google's token endpoint and account lookup stubbed. Returns the
    redirect_uri each code exchange used."""
    import datetime as dt

    from othor.connectors.sheets import client as sheets_client
    from othor.connectors.sheets import oauth

    settings = env.app.state.services.settings  # type: ignore[attr-defined]
    settings.sheets_mode = "google"
    settings.google_client_id = "cid.apps.googleusercontent.com"
    used: list[str] = []

    def exchange(_s: Any, code: str, redirect_uri: str, http: Any = None) -> Tokens:
        used.append(redirect_uri)
        return Tokens("at-" + code, "rt", dt.datetime(2026, 11, 1, tzinfo=dt.UTC))

    monkeypatch.setattr(oauth, "exchange", exchange)
    monkeypatch.setattr(
        sheets_client.GoogleSheetsClient, "account", lambda self: "uday@indigothread.example"
    )
    return used


def _start(env: Env, host: str) -> dict[str, list[str]]:
    tok = env.csrf(f"{host}/setup/sources/sheets")
    r = env.client.post(
        f"{host}/sources/google/start",
        data={"csrf": tok, "setup": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith("https://accounts.google.com/")
    return parse_qs(urlparse(r.headers["location"]).query)


def _state(env: Env, data: dict[str, str]) -> str:
    svc = env.app.state.services  # type: ignore[attr-defined]
    return str(svc.signer("google-oauth").dumps(data, svc.clock()))


def test_the_redirect_uri_is_the_root_host_whatever_host_started(env: Env, google) -> None:
    _to_sources(env)
    q = _start(env, env.host("indigothread"))
    assert q["redirect_uri"] == [CALLBACK]


def test_the_root_callback_hands_back_to_the_company_host_and_finishes(env: Env, google) -> None:
    _to_sources(env)
    h = env.host("indigothread")
    state = _start(env, h)["state"][0]
    r = env.client.get(CALLBACK, params={"code": "c1", "state": state}, follow_redirects=False)
    assert r.status_code == 303
    to = urlparse(r.headers["location"])
    assert f"{to.scheme}://{to.netloc}" == h and to.path == "/sources/google/finish"
    r = env.client.get(r.headers["location"], follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/setup/sources/sheets?sid=")
    # the code is exchanged against the same redirect URI Google was given
    assert google == [CALLBACK]


def test_a_tampered_or_stale_state_goes_nowhere(env: Env, google) -> None:
    _to_sources(env)
    good = _start(env, env.host("indigothread"))["state"][0]
    for state in (good[:-2] + "xx", "", "nonsense"):
        r = env.client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)
        assert r.status_code == 404 and "location" not in r.headers
    assert google == []


def test_one_companys_state_cant_finish_on_another(env: Env, google) -> None:
    signup(env, email="meera@tanvi-foods.example", company="Tanvi Foods", slug="tanvi")
    env.client.cookies.clear()
    _to_sources(env)  # signed in to indigothread
    h = env.host("indigothread")
    other = _state(env, {"c": "00000000-0000-0000-0000-000000000001", "s": "tanvi"})
    r = env.client.get(f"{h}/sources/google/finish", params={"code": "c", "state": other})
    assert google == []
    # and the root host sends it to the company the state names, not the one asking
    r = env.client.get(CALLBACK, params={"code": "c", "state": other}, follow_redirects=False)
    assert urlparse(r.headers["location"]).netloc.startswith("tanvi.")
    assert google == []


def test_a_callback_on_a_company_host_is_not_found(env: Env, google) -> None:
    """Only the root host is registered at Google; the company-host path isn't a way in."""
    _to_sources(env)
    h = env.host("indigothread")
    state = _start(env, h)["state"][0]
    r = env.client.get(
        f"{h}/sources/google/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert r.status_code == 404 and google == []


def test_both_sheets_pages_load_the_real_picker(env: Env, google) -> None:
    """11 Nov 2026: on production the Picker did nothing in setup (the page's policy blocked
    Google's script) and the Sources page's panel was fixture-only. Both now load it."""
    from othor.web.middleware import SCRIPT_HOSTS

    _to_sources(env)
    h = env.host("indigothread")
    state = _start(env, h)["state"][0]
    r = env.client.get(CALLBACK, params={"code": "c1", "state": state}, follow_redirects=False)
    setup_url = env.client.get(r.headers["location"], follow_redirects=False).headers["location"]
    sid = parse_qs(urlparse(setup_url).query)["sid"][0]
    for url in (f"{h}{setup_url}", f"{h}/sources?panel=sheets&s={sid}"):
        page = env.client.get(url)
        assert page.status_code == 200, url
        assert "https://apis.google.com/js/api.js" in page.text, url
        assert "data-picker" in page.text and 'id="picked-files"' in page.text, url
        assert SCRIPT_HOSTS in page.headers["content-security-policy"], url
