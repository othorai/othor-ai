# SPDX-License-Identifier: Apache-2.0
"""Sign-up by invitation and Request access (M11d part C).

Without a usable invitation, /signup asks the visitor to request access and never says why a
code failed; with one, the normal sign-up runs with the invited email, and the invitation is
used in the same transaction that creates the company. Codes are stored hashed.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from othor.core.auth import AuthService
from othor.core.config import Settings
from othor.core.models import AccessRequest, Company, Invitation, PlatformEvent
from othor.mail.relay import MemoryRelay
from othor.migrations.runner import upgrade
from othor.web.app import create_app
from tests.needs import needs_site
from tests.web.conftest import BASE, ROOT, Clock, Env

ASK = "Othor AI is by invitation for now"
BAD = "This invitation can’t be used"


@pytest.fixture
def ienv(tmp_path: Path) -> Env:
    url = f"sqlite:///{tmp_path / 'web.db'}"
    upgrade(url)
    relay, clock = MemoryRelay(), Clock()
    settings = Settings(
        _env_file=None,
        db_url=url,
        data_dir=str(tmp_path),
        env="test",  # type: ignore[arg-type]
        base_domain=BASE,
        mail_relay="memory",
        mail_from_domain="othor.localhost",
        public_site=True,
        signup="invite",
    )
    app = create_app(settings, relay=relay, clock=clock)
    with TestClient(app, base_url=ROOT) as client:
        yield Env(client, relay, clock, app)


def _auth(e: Env) -> AuthService:
    return e.app.state.services.auth  # type: ignore[attr-defined,no-any-return]


def _db(e: Env) -> sa.orm.Session:
    return e.app.state.services.sf()  # type: ignore[attr-defined,no-any-return]


def _signup(e: Env, code: str, email: str, slug: str = "indigothread") -> object:
    """1b with an invitation → 1f → 2a. Returns the last response."""
    c = e.client
    tok = e.csrf(f"{ROOT}/signup?invite={code}")
    r = c.post(
        f"{ROOT}/signup",
        data={
            "csrf": tok,
            "name": "Uday Kamath",
            "email": email,
            "company": "Indigo Thread Co.",
            "invite": code,
        },
        follow_redirects=False,
    )
    if r.status_code != 303:
        return r
    r = c.post(
        f"{ROOT}/signup/verify",
        data={"csrf": tok, "code": e.last_code(email.lower())},
        follow_redirects=False,
    )
    return c.post(
        f"{ROOT}/signup/address",
        data={
            "csrf": tok,
            "slug": slug,
            "website": "",
            "fy": "4",
            "currency": "INR",
            "tz": "Asia/Kolkata",
        },
        follow_redirects=False,
    )


def _companies(e: Env) -> int:
    with _db(e) as s:
        return len(s.scalars(sa.select(Company.id)).all())


def _actions(e: Env) -> list[str]:
    with _db(e) as s:
        return [ev.action for ev in s.scalars(sa.select(PlatformEvent).order_by(PlatformEvent.at))]


# --- what the visitor sees -------------------------------------------------------------------


@needs_site
def test_no_code_asks_for_access(ienv: Env) -> None:
    r = ienv.client.get(f"{ROOT}/signup")
    assert r.status_code == 200 and ASK in r.text
    assert 'action="/request-access"' in r.text and 'name="email"' in r.text
    assert "Create company" not in r.text


@pytest.mark.parametrize("case", ["bad", "expired", "used", "revoked"])
def test_unusable_codes_look_the_same_and_create_nothing(ienv: Env, case: str) -> None:
    auth = _auth(ienv)
    info, code = auth.create_invitation("uk@indigothread.example")
    if case == "bad":
        code = "not-a-real-code"
    elif case == "expired":
        ienv.clock.tick(days=15)
    elif case == "used":
        assert _signup(ienv, code, "uk@indigothread.example").status_code == 303
        ienv.client.cookies.clear()
    else:
        auth.revoke_invitation(info.id)
    before = _companies(ienv)
    r = ienv.client.get(f"{ROOT}/signup?invite={code}")
    assert r.status_code == 200 and BAD in r.text and "Ask for a new one" in r.text
    for word in ("expired", "used", "revoked", "unknown"):
        assert word not in r.text.lower().split(BAD.lower(), 1)[1].split("</form>", 1)[0]
    r = _signup(ienv, code, "uk@indigothread.example", slug="another")
    assert r.status_code == 400 and BAD in r.text  # type: ignore[attr-defined]
    assert _companies(ienv) == before


def test_a_valid_code_signs_up_once(ienv: Env) -> None:
    _, code = _auth(ienv).create_invitation(
        "UK@IndigoThread.example", company_name="Indigo Thread Co."
    )
    page = ienv.client.get(f"{ROOT}/signup?invite={code}").text
    assert "Create company" in page and 'value="uk@indigothread.example"' in page
    assert f'name="invite" value="{code}"' in page
    r = _signup(ienv, code, "Uk@indigothread.example")
    assert r.status_code == 303 and r.headers["location"].startswith("http://indigothread.")  # type: ignore[attr-defined]
    assert _companies(ienv) == 1
    ienv.client.cookies.clear()
    r = _signup(ienv, code, "uk@indigothread.example", slug="second")
    assert r.status_code == 400 and _companies(ienv) == 1  # type: ignore[attr-defined]
    with _db(ienv) as s:
        inv = s.scalars(sa.select(Invitation)).one()
        assert inv.used_at is not None and inv.used_by_company is not None
    assert "invitation.used" in _actions(ienv)


def test_a_different_email_is_refused(ienv: Env) -> None:
    _, code = _auth(ienv).create_invitation("uk@indigothread.example")
    r = _signup(ienv, code, "someone@else.example")
    assert r.status_code == 400  # type: ignore[attr-defined]
    assert "Use the email address the invitation was sent to." in r.text  # type: ignore[attr-defined]
    assert _companies(ienv) == 0


def test_codes_are_stored_hashed_and_never_logged(
    ienv: Env, caplog: pytest.LogCaptureFixture
) -> None:
    from othor.core.logging import redact

    caplog.set_level(logging.DEBUG)
    _, code = _auth(ienv).create_invitation("uk@indigothread.example")
    assert len(code) >= 22  # 128 bits, URL-safe
    _signup(ienv, code, "uk@indigothread.example")
    with _db(ienv) as s:
        inv = s.scalars(sa.select(Invitation)).one()
    assert inv.code_hash == hashlib.sha256(code.encode()).hexdigest()
    app_lines = [
        r.getMessage() + str(r.__dict__) for r in caplog.records if r.name.startswith("othor")
    ]
    assert app_lines and not any(code in line for line in app_lines)
    # the access log (uvicorn, through the redacting handler) never shows the code
    assert code not in redact(f'"GET /signup?invite={code} HTTP/1.1" 200')


def test_open_signup_keeps_m1(env: Env) -> None:
    from tests.web.conftest import signup

    assert env.app.state.services.settings.signup == "open"  # type: ignore[attr-defined]
    r = signup(env)
    assert r.status_code in (302, 303)  # type: ignore[attr-defined]


# --- Request access --------------------------------------------------------------------------


def _ask(e: Env, **kw: str) -> object:
    data = {
        "name": "Meera Iyer",
        "email": "meera@tanvi-foods.example",
        "company": "Tanvi Foods",
        "team_size": "200-999",
        "runs_today": "Tally and a sales spreadsheet",
        "message": "",
    }
    data |= kw
    data["csrf"] = e.csrf(f"{ROOT}/request-access")
    return e.client.post(f"{ROOT}/request-access", data=data, follow_redirects=False)


@needs_site
def test_a_request_is_stored_and_emailed_once_a_day(ienv: Env) -> None:
    r = _ask(ienv, message="Receivables across three plants")
    assert r.status_code == 200 and "Thanks. We’ll be in touch." in r.text  # type: ignore[attr-defined]
    r = _ask(ienv)  # a double submit
    assert r.status_code == 200 and "Thanks." in r.text  # type: ignore[attr-defined]
    with _db(ienv) as s:
        rows = s.scalars(sa.select(AccessRequest)).all()
    assert len(rows) == 1 and rows[0].company == "Tanvi Foods"
    assert (
        rows[0].team_size == "200 to 999" and rows[0].runs_today == "Tally and a sales spreadsheet"
    )
    mails = [m for m in ienv.relay.outbox if m.to == "support@othor.ai"]
    assert len(mails) == 1 and "Tanvi Foods" in mails[0].subject
    assert mails[0].reply_to == "meera@tanvi-foods.example"
    assert "Receivables across three plants" in mails[0].text
    assert "People who work there: 200 to 999" in mails[0].text
    assert "What they run today: Tally and a sales spreadsheet" in mails[0].text
    assert _actions(ienv).count("access.requested") == 1
    ienv.clock.tick(days=1)
    _ask(ienv)
    assert len([m for m in ienv.relay.outbox if m.to == "support@othor.ai"]) == 2


@needs_site
def test_a_request_needs_every_answer_but_anything_else(ienv: Env) -> None:
    r = _ask(ienv, name="", email="nope", company="", team_size="lots", runs_today="")
    assert r.status_code == 400  # type: ignore[attr-defined]
    for line in (
        "Enter your name.",
        "Enter your work email.",
        "Enter the company name.",
        "Choose how many people work there.",
        "Tell us what you run today.",
    ):
        assert line in r.text  # type: ignore[attr-defined]
    assert ienv.relay.outbox == []


@needs_site
def test_the_honeypot_stores_nothing(ienv: Env) -> None:
    r = _ask(ienv, website="http://spam.example")
    assert r.status_code == 200 and "Thanks." in r.text  # type: ignore[attr-defined]
    with _db(ienv) as s:
        assert s.scalars(sa.select(AccessRequest)).all() == []
    assert ienv.relay.outbox == []


@needs_site
def test_a_request_needs_the_csrf_token(ienv: Env) -> None:
    ienv.client.get(f"{ROOT}/request-access")
    r = ienv.client.post(
        f"{ROOT}/request-access", data={"name": "x", "email": "x@y.example", "company": "x"}
    )
    assert r.status_code in (400, 403)


def test_platform_events_are_append_only(ienv: Env) -> None:
    _auth(ienv).create_invitation("uk@indigothread.example")
    with _db(ienv) as s:
        ev = s.scalars(sa.select(PlatformEvent)).first()
        assert ev is not None and ev.action == "invitation.created"
        with pytest.raises(Exception, match="append-only"):
            s.execute(sa.update(PlatformEvent).values(action="x"))
            s.commit()


# --- the operator's command line -------------------------------------------------------------


def test_the_cli_creates_lists_and_revokes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from othor.cli import app
    from othor.core.config import get_settings

    url = f"sqlite:///{tmp_path / 'cli.db'}"
    upgrade(url)
    for k, v in {
        "OTHOR_DB_URL": url,
        "OTHOR_DATA_DIR": str(tmp_path),
        "OTHOR_ENV": "test",
        "OTHOR_BASE_DOMAIN": "othor.ai",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    try:
        run = CliRunner().invoke
        r = run(app, ["admin", "invite", "--email", "uk@indigothread.example", "--days", "7"])
        assert r.exit_code == 0, r.output
        link = r.stdout.strip().splitlines()[0]
        assert link.startswith("https://othor.ai/signup?invite=")
        r = run(app, ["admin", "invites", "--json"])
        assert r.exit_code == 0 and "uk@indigothread.example" in r.stdout and '"open"' in r.stdout
        inv_id = r.stdout.split('"id": "', 1)[1].split('"', 1)[0]
        r = run(app, ["admin", "invite", "--revoke", inv_id])
        assert r.exit_code == 0 and "revoked" in r.stdout
        from othor.core.db import make_engine, make_session_factory

        with make_session_factory(make_engine(url))() as s:
            actions = [e.action for e in s.scalars(sa.select(PlatformEvent))]
        assert actions == ["invitation.created", "invitation.revoked"]
    finally:
        get_settings.cache_clear()
