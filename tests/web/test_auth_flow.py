# SPDX-License-Identifier: Apache-2.0
"""Accept (M1): create Indigo Thread, sign out, sign in by code, resume at the saved step."""

import re

from tests.web.conftest import ROOT, Env, signup


def test_signup_signout_signin_resumes(env: Env) -> None:
    c = env.client
    host = env.host("indigothread")
    r = signup(env)
    assert r.status_code == 303 and r.headers["location"] == "/setup/upload"  # type: ignore[attr-defined]
    page = c.get(f"{host}/setup/upload")
    assert page.status_code == 200 and "Set up Indigo Thread Co." in page.text
    assert 'aria-current="step"' in page.text
    # sign out
    tok = env.csrf(f"{host}/setup/upload")
    r = c.post(f"{host}/logout", data={"csrf": tok}, follow_redirects=False)
    assert r.headers["location"] == "/login?signed_out=1"
    assert "You’ve been signed out." in c.get(f"{host}/login?signed_out=1").text
    assert c.get(f"{host}/settings", follow_redirects=False).headers["location"] == "/login"
    # sign in by code (1b → 1c)
    tok = env.csrf(f"{host}/login")
    r = c.post(
        f"{host}/login",
        data={"csrf": tok, "email": "UK@indigothread.example"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/login/code"
    assert "Check your email" in c.get(f"{host}/login/code").text
    code = env.last_code("uk@indigothread.example")
    digits = {f"code{i}": d for i, d in enumerate(code)}
    r = c.post(f"{host}/login/code", data={"csrf": tok, **digits}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup/upload"
    actions = [e.action for e in env.events()]
    for a in (
        "company.created",
        "user.signup",
        "onboarding.step_saved",
        "auth.signed_in",
        "auth.signed_out",
        "auth.code_sent",
    ):
        assert a in actions, a


def test_continue_saves_the_step_and_resume_follows_it(env: Env) -> None:
    signup(env)
    c, host = env.client, env.host("indigothread")
    tok = env.csrf(f"{host}/setup/upload")
    r = c.post(f"{host}/setup/upload/continue", data={"csrf": tok}, follow_redirects=False)
    assert r.headers["location"] == "/setup/business"
    assert c.get(f"{host}/", follow_redirects=False).headers["location"] == "/setup/business"
    assert (
        c.get(f"{host}/setup/metrics", follow_redirects=False).headers["location"]
        == "/setup/business"
    )


def test_code_states_1d(env: Env) -> None:
    signup(env)
    c, host = env.client, env.host("indigothread")
    tok = env.csrf(f"{host}/setup/upload")
    c.post(f"{host}/logout", data={"csrf": tok})
    tok = env.csrf(f"{host}/login")
    c.post(f"{host}/login", data={"csrf": tok, "email": "uk@indigothread.example"})
    code = env.last_code()
    wrong = "000000" if code != "000000" else "111111"
    r = c.post(f"{host}/login/code", data={"csrf": tok, "code": wrong})
    assert (
        r.status_code == 400 and "Wrong code. 4 attempts left." in r.text and "is-error" in r.text
    )
    # #41: the first box has focus, so the next key starts the code over
    assert re.search(r'aria-label="Digit 1"[^>]*autofocus', r.text)
    r = c.post(f"{host}/login/resend", data={"csrf": tok})
    assert "New code sent. Resend in" in r.text  # cooldown
    env.clock.tick(minutes=11)
    r = c.post(f"{host}/login/code", data={"csrf": tok, "code": code})
    assert "Code expired." in r.text and "Send a new code" in r.text
    r = c.post(f"{host}/login/resend", data={"csrf": tok})
    for _ in range(5):
        r = c.post(f"{host}/login/code", data={"csrf": tok, "code": wrong})
    assert r.status_code == 429 and "Too many attempts. Try again in 15 minutes." in r.text
    assert "Contact the company owner" in r.text


def test_1h_not_a_member(env: Env) -> None:
    signup(env)
    c, host = env.client, env.host("indigothread")
    tok = env.csrf(f"{host}/login")
    r = c.post(f"{host}/login", data={"csrf": tok, "email": "ravi@indigothread.example"})
    assert r.status_code == 400 and "This email isn’t part of Indigo Thread Co.." in r.text
    assert all(m.to != "ravi@indigothread.example" for m in env.relay.outbox)  # no code sent


def test_root_address_and_recent_chips(env: Env) -> None:
    signup(env)
    c = env.client
    tok = env.csrf(f"{ROOT}/login")
    r = c.post(f"{ROOT}/login", data={"csrf": tok, "slug": "indigothread"}, follow_redirects=False)
    assert r.headers["location"] == f"{env.host('indigothread')}/login"
    assert "indigothread.othor.localhost" in c.get(f"{ROOT}/login").text  # Recent chip
    r = c.post(f"{ROOT}/login", data={"csrf": tok, "slug": "nobody"})
    assert r.status_code == 400 and "There’s no company at nobody.othor.localhost." in r.text


def test_unknown_company_host_is_404(env: Env) -> None:
    r = env.client.get(f"{env.host('nobody')}/login")
    assert r.status_code == 404 and "There’s no company at" in r.text


def test_slug_rules_on_2a(env: Env) -> None:
    signup(env)
    c = env.client
    for slug, msg in (
        ("indigothread", "is taken"),
        ("ab", "3–30 lowercase"),
        ("admin", "is reserved"),
        ("new-co", "is available"),
    ):
        assert msg in c.get(f"{ROOT}/signup/address/check", params={"slug": slug}).text, slug


def test_post_without_csrf_is_refused(env: Env) -> None:
    r = env.client.post(f"{ROOT}/signup", data={"name": "x", "email": "a@b.in", "company": "c"})
    assert r.status_code == 403


def test_handoff_is_single_use(env: Env) -> None:
    c = env.client
    tok = env.csrf(f"{ROOT}/signup")
    c.post(
        f"{ROOT}/signup", data={"csrf": tok, "name": "A", "email": "a@acme.in", "company": "Acme"}
    )
    c.post(f"{ROOT}/signup/verify", data={"csrf": tok, "code": env.last_code()})
    r = c.post(
        f"{ROOT}/signup/address",
        data={
            "csrf": tok,
            "slug": "acme",
            "website": "",
            "fy": "4",
            "currency": "INR",
            "tz": "Asia/Kolkata",
        },
        follow_redirects=False,
    )
    url = r.headers["location"]
    assert c.get(url, follow_redirects=False).headers["location"] == "/setup/upload"
    c.cookies.clear()
    assert c.get(url, follow_redirects=False).headers["location"] == "/login"


def test_the_first_code_box_has_the_cursor(env: Env) -> None:
    """1c (#15): the box that looks focused is focused, so the code can be typed without a click."""
    import re

    signup(env)
    host = env.host("indigothread")
    c = env.client
    tok = env.csrf(f"{host}/settings")
    c.post(f"{host}/logout", data={"csrf": tok})
    tok = env.csrf(f"{host}/login")
    c.post(f"{host}/login", data={"csrf": tok, "email": "uk@indigothread.example"})
    page = c.get(f"{host}/login/code").text
    boxes = re.findall(r"<input class=\"digit[^>]*>", page)
    assert len(boxes) == 6
    assert "autofocus" in boxes[0] and "is-focus" in boxes[0]
    assert not any("autofocus" in b for b in boxes[1:])


def test_the_code_sent_twice_still_lands_signed_in(env: Env) -> None:
    """#117 (owner, 24 Nov 2026, Firefox): the code form submitted twice (the sixth digit
    submits it, then Continue, Enter or autofill again). The first signs in and clears the
    sign-in email; the second must land on the company, not back on the email field."""
    signup(env)
    c, host = env.client, env.host("indigothread")
    tok = env.csrf(f"{host}/setup/upload")
    c.post(f"{host}/logout", data={"csrf": tok}, follow_redirects=False)
    tok = env.csrf(f"{host}/login")
    c.post(f"{host}/login", data={"csrf": tok, "email": "uk@indigothread.example"})
    code = env.last_code("uk@indigothread.example")
    digits = {f"code{i}": d for i, d in enumerate(code)}
    first = c.post(f"{host}/login/code", data={"csrf": tok, **digits}, follow_redirects=False)
    assert first.headers["location"] == "/setup/upload"
    again = c.post(f"{host}/login/code", data={"csrf": tok, **digits}, follow_redirects=False)
    assert again.status_code == 303 and again.headers["location"] == "/setup/upload"
    # without a session, a stray code post still goes to the email step
    c.cookies.clear()
    tok = env.csrf(f"{host}/login")
    stray = c.post(f"{host}/login/code", data={"csrf": tok, **digits}, follow_redirects=False)
    assert stray.headers["location"] == "/login"
