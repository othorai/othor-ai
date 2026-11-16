# SPDX-License-Identifier: Apache-2.0
"""Per-IP limits on sign-in and signup (M11a; OPEN-QUESTIONS L40)."""

from __future__ import annotations

import pytest

from othor.core.ratelimit import CODES, LIMITED_TEXT, LOOKUP
from tests.web.conftest import ROOT, Env, signup


def test_company_lookups_are_limited_per_network(env: Env) -> None:
    tok = env.csrf(f"{ROOT}/login")
    for _ in range(LOOKUP.max):
        r = env.client.post(f"{ROOT}/login", data={"csrf": tok, "slug": "nobody"})
        assert r.status_code != 429 and "There’s no company at nobody" in r.text
    r = env.client.post(f"{ROOT}/login", data={"csrf": tok, "slug": "nobody"})
    assert r.status_code == 429 and LIMITED_TEXT in r.text
    # behind Fly's proxy only, another client IP is another network
    env.app.state.services.settings.trust_proxy = "fly"  # type: ignore[attr-defined]
    r = env.client.post(
        f"{ROOT}/login", data={"csrf": tok, "slug": "nobody"}, headers={"Fly-Client-IP": "1.1.1.1"}
    )
    assert r.status_code != 429


def test_code_attempts_are_limited_and_logged(env: Env) -> None:
    signup(env)
    env.client.cookies.clear()
    h = env.host("indigothread")
    tok = env.csrf(f"{h}/login")
    codes = [
        env.client.post(f"{h}/login", data={"csrf": tok, "email": f"x{i}@probe.in"}).status_code
        for i in range(CODES.max + 1)
    ]
    # signup's own two code requests count in the same window
    first = codes.index(429)
    assert first == CODES.max - 2 and set(codes[first:]) == {429}
    assert env.events("auth.rate_limited")


def test_address_check_toast(env: Env) -> None:
    for _ in range(LOOKUP.max):
        env.client.get(f"{ROOT}/signup/address/check?slug=abc", headers={"HX-Request": "true"})
    r = env.client.get(f"{ROOT}/signup/address/check?slug=abc", headers={"HX-Request": "true"})
    assert r.status_code == 429 and r.headers["x-othor-toast"] == "1"


@pytest.mark.parametrize("proxy", ["alb", "caddy"])
def test_alb_mode_reads_the_last_forwarded_hop(env: Env, proxy: str) -> None:
    """M11b: behind the ALB (or Caddy in the self-host compose, M20.4), the client is the hop
    the proxy appended (the last one); anything the client sent before it can't make it look
    like another network."""
    settings = env.app.state.services.settings  # type: ignore[attr-defined]
    settings.trust_proxy = proxy
    tok = env.csrf(f"{ROOT}/login")
    spoofed = [
        env.client.post(
            f"{ROOT}/login",
            data={"csrf": tok, "slug": "nobody"},
            headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
        ).status_code
        for i in range(LOOKUP.max + 1)
    ]
    assert spoofed[-1] == 429 and 429 not in spoofed[:-1]
    r = env.client.post(
        f"{ROOT}/login",
        data={"csrf": tok, "slug": "nobody"},
        headers={"X-Forwarded-For": "203.0.113.7, 198.51.100.9"},
    )
    assert r.status_code != 429


def test_fly_header_means_nothing_in_alb_mode(env: Env) -> None:
    from starlette.requests import Request

    from othor.web.errors import client_ip

    env.app.state.services.settings.trust_proxy = "alb"  # type: ignore[attr-defined]

    def req(headers: dict[str, str]) -> Request:
        scope = {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": ("10.0.1.5", 5000),
            "app": env.app,
        }
        return Request(scope)

    assert client_ip(req({"Fly-Client-IP": "1.1.1.1"})) == "10.0.1.5"
    assert client_ip(req({"X-Forwarded-For": " 9.9.9.9 ,  8.8.8.8 "})) == "8.8.8.8"
    assert client_ip(req({"X-Forwarded-For": ""})) == "10.0.1.5"


def test_public_scheme_follows_the_alb_only_in_alb_mode(env: Env) -> None:
    """The ALB ends TLS and forwards plain HTTP with X-Forwarded-Proto: https. Links built from
    the request (root_url: Open Graph, canonical, redirects to the root) must say https then,
    and only when the proxy is trusted."""
    settings = env.app.state.services.settings  # type: ignore[attr-defined]
    settings.public_site = True
    signup(env)
    h = env.host("indigothread")

    def where(proto: str) -> str:
        r = env.client.get(
            f"{h}/privacy", headers={"X-Forwarded-Proto": proto}, follow_redirects=False
        )
        assert r.status_code == 308
        return str(r.headers["location"])

    assert where("https").startswith("http://")  # not trusted: the header is ignored
    for proxy in ("alb", "caddy"):  # Caddy: the self-host compose (M20.4)
        settings.trust_proxy = proxy
        assert where("https").startswith("https://")
        assert where("gopher").startswith("http://")
