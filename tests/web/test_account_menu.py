# SPDX-License-Identifier: Apache-2.0
"""The shared account menu (4d1): name, email · Owner, divider, Sign out → 1g."""

from tests.web.conftest import Env, signup


def test_menu_in_the_app_shell(env: Env) -> None:
    signup(env)
    c, host = env.client, env.host("indigothread")
    html = c.get(f"{host}/settings").text
    assert 'aria-label="Account menu for Uday Kamath"' in html
    assert "uk@indigothread.example · Owner" in html
    assert ">Sign out</button>" in html and "Log out" not in html
    assert "Sign out everywhere" not in html
    assert "⌘K" not in html  # Search is on the roadmap, not in v0.1


def test_sign_out_from_the_menu_goes_to_1g(env: Env) -> None:
    signup(env)
    c, host = env.client, env.host("indigothread")
    tok = env.csrf(f"{host}/settings")
    r = c.post(f"{host}/logout", data={"csrf": tok}, follow_redirects=False)
    assert r.headers["location"] == "/login?signed_out=1"
    assert "You’ve been signed out." in c.get(f"{host}{r.headers['location']}").text
    assert c.get(f"{host}/settings", follow_redirects=False).headers["location"] == "/login"
    assert env.events("auth.signed_out")
