# SPDX-License-Identifier: Apache-2.0
"""Guard 5 (docs/11), over HTTP: company A's session on company B's host → 404."""

from tests.web.conftest import BASE, Env, signup


def test_other_companys_session_gets_404(env: Env) -> None:
    signup(env, email="uk@indigothread.example", company="Indigo Thread Co.", slug="indigothread")
    a_cookie = next(c.value for c in env.client.cookies.jar if c.name == "othor_session")
    env.client.cookies.clear()
    signup(env, name="Other Owner", email="owner@otherco.in", company="Other Co", slug="otherco")
    env.client.cookies.clear()
    # Present A's session cookie to B's host (a browser never would: cookies are host-only).
    env.client.cookies.set("othor_session", a_cookie, domain=f"otherco.{BASE}")
    b = env.host("otherco")
    for path in (
        "/settings",
        "/setup/upload",
        "/home",
        "/summary",
        "/sources",
        "/setup/sources",
        "/sources?panel=add",
    ):
        r = env.client.get(f"{b}{path}", follow_redirects=False)
        assert r.status_code == 404, path
        assert "Other Co" not in r.text
    env.client.cookies.clear()
    env.client.cookies.set("othor_session", a_cookie, domain=f"indigothread.{BASE}")
    assert env.client.get(f"{env.host('indigothread')}/settings").status_code == 200
