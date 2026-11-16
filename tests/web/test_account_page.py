# SPDX-License-Identifier: Apache-2.0
"""The Account page (M15.8, roadmap Q5.5): your name (saved in place, an event), your sessions on
this company with "This device", and Sign out everywhere; the account menu links to it."""

from __future__ import annotations

import sqlalchemy as sa

from othor.core.models import AppUser, Company, Membership
from othor.core.tenancy import system_scope
from tests.web.conftest import Env, signup

MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Safari/537.36"
)
LINUX_FF = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"


def _ids(env: Env) -> tuple:
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        cid = s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one()
        uid = s.scalars(
            sa.select(AppUser.id).where(AppUser.email == "uk@indigothread.example")
        ).one()
    return cid, uid


def test_the_account_menu_links_to_the_page(env: Env) -> None:
    signup(env)
    html = env.client.get(env.host("indigothread") + "/settings").text
    assert 'href="/account"' in html and ">Account</a>" in html


def test_the_page_shows_your_name_email_and_sessions(env: Env) -> None:
    signup(env)
    cid, uid = _ids(env)
    auth = env.app.state.services.auth  # type: ignore[attr-defined]
    auth.create_session(uid, cid, MAC)  # another device
    # someone else's session on this company is never listed
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        other = AppUser(email="ravi@indigothread.example", name="Ravi")
        s.add(other)
        s.flush()
        s.add(Membership(company_id=cid, user_id=other.id, role="owner"))
        s.commit()
        oid = other.id
    auth.create_session(oid, cid, LINUX_FF)
    page = env.client.get(env.host("indigothread") + "/account").text
    assert "<h1>Account</h1>" in page
    assert 'value="Uday Kamath"' in page and "uk@indigothread.example" in page
    assert "Your email signs you in; it can’t be changed here." in page
    assert page.count('<tr class="acct-session"') == 2
    assert "Chrome on macOS" in page and "Firefox on Linux" not in page
    assert page.count("This device") == 1
    assert "Sign out everywhere" in page and "Signs you out on every device, this one too." in page


def test_your_name_saves_in_place_with_an_event(env: Env) -> None:
    signup(env)
    h = env.host("indigothread")
    tok = env.csrf(h + "/account")
    r = env.client.post(
        h + "/account/name", data={"csrf": tok, "name": "  Uday K  "}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"] == "/account?saved=1"
    page = env.client.get(h + r.headers["location"]).text
    assert 'value="Uday K"' in page and "Name saved" in page
    ev = env.events("user.renamed")[-1]
    assert ev.before == {"name": "Uday Kamath"} and ev.after == {"name": "Uday K"}
    blank = env.client.post(
        h + "/account/name", data={"csrf": tok, "name": "  "}, follow_redirects=False
    )
    assert blank.headers["location"] == "/account?error=name"
    assert "Enter your name." in env.client.get(h + "/account?error=name").text
    assert len(env.events("user.renamed")) == 1
    assert env.client.post(h + "/account/name", data={"name": "x"}).status_code == 403  # CSRF


def test_sign_out_everywhere_ends_every_session(env: Env) -> None:
    signup(env)
    cid, uid = _ids(env)
    env.app.state.services.auth.create_session(uid, cid, MAC)  # type: ignore[attr-defined]
    h = env.host("indigothread")
    tok = env.csrf(h + "/account")
    r = env.client.post(h + "/logout/everywhere", data={"csrf": tok}, follow_redirects=False)
    assert r.headers["location"] == "/login?signed_out=1"
    assert env.events("auth.signed_out_everywhere")[-1].after == {"sessions_ended": 2}
    assert env.client.get(h + "/account", follow_redirects=False).headers["location"] == "/login"
