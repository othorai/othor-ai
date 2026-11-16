# SPDX-License-Identifier: Apache-2.0
"""Email codes, sessions and the signup handoff (docs/01 §2, boards 1c/1d)."""

import datetime as dt
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core import auth
from othor.core.crypto import InstanceKey
from othor.core.models import AppUser, AuthSession, LoginCode
from othor.core.tenancy import system_scope

T0 = dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.UTC)
Ids = tuple[uuid.UUID, uuid.UUID]


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> dt.datetime:
        return self.t

    def tick(self, **kw: float) -> None:
        self.t += dt.timedelta(**kw)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def key(tmp_path: Path) -> InstanceKey:
    return InstanceKey.load_or_create(tmp_path / "k", allow_create=True)


@pytest.fixture
def svc(session_factory: sessionmaker[Session], key: InstanceKey, clock: Clock) -> auth.AuthService:
    return auth.AuthService(session_factory, key, clock=clock)


def test_code_is_six_digits_and_stored_hashed(
    svc: auth.AuthService, session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, _ = two_companies
    issued = svc.issue_code("UK@indigothread.example", "signin", company_id=a)
    assert len(issued.code) == 6 and issued.code.isdigit()
    with session_factory() as s:
        row = s.scalars(sa.select(LoginCode)).one()
    assert row.email == "uk@indigothread.example"
    assert issued.code not in row.code_hash
    assert row.expires_at == T0 + dt.timedelta(minutes=10)


def test_verify_once(svc: auth.AuthService, two_companies: Ids) -> None:
    a, _ = two_companies
    issued = svc.issue_code("uk@indigothread.example", "signin", company_id=a)
    svc.verify_code("uk@indigothread.example", "signin", issued.code, company_id=a)
    with pytest.raises(auth.NoCode):
        svc.verify_code("uk@indigothread.example", "signin", issued.code, company_id=a)


def test_code_is_bound_to_company_and_purpose(svc: auth.AuthService, two_companies: Ids) -> None:
    a, b = two_companies
    issued = svc.issue_code("uk@indigothread.example", "signin", company_id=a)
    with pytest.raises(auth.NoCode):
        svc.verify_code("uk@indigothread.example", "signin", issued.code, company_id=b)
    with pytest.raises(auth.NoCode):
        svc.verify_code("uk@indigothread.example", "signup", issued.code)


def test_wrong_code_counts_down_then_locks_15_minutes(
    svc: auth.AuthService, clock: Clock, two_companies: Ids
) -> None:
    a, _ = two_companies
    issued = svc.issue_code("uk@indigothread.example", "signin", company_id=a)
    wrong = "000000" if issued.code != "000000" else "111111"
    for left in (4, 3, 2, 1):
        with pytest.raises(auth.WrongCode) as e:
            svc.verify_code("uk@indigothread.example", "signin", wrong, company_id=a)
        assert e.value.attempts_left == left
    with pytest.raises(auth.Locked) as e2:
        svc.verify_code("uk@indigothread.example", "signin", wrong, company_id=a)
    assert e2.value.until == T0 + dt.timedelta(minutes=15)
    with pytest.raises(auth.Locked):  # even the right code, and no new code while locked
        svc.verify_code("uk@indigothread.example", "signin", issued.code, company_id=a)
    with pytest.raises(auth.Locked):
        svc.issue_code("uk@indigothread.example", "signin", company_id=a)
    clock.tick(minutes=15, seconds=1)
    fresh = svc.issue_code("uk@indigothread.example", "signin", company_id=a)
    svc.verify_code("uk@indigothread.example", "signin", fresh.code, company_id=a)


def test_expires_after_10_minutes(svc: auth.AuthService, clock: Clock, two_companies: Ids) -> None:
    a, _ = two_companies
    issued = svc.issue_code("uk@indigothread.example", "signin", company_id=a)
    clock.tick(minutes=10, seconds=1)
    with pytest.raises(auth.Expired):
        svc.verify_code("uk@indigothread.example", "signin", issued.code, company_id=a)


def test_resend_after_45_seconds_replaces_the_old_code(
    svc: auth.AuthService, clock: Clock, two_companies: Ids
) -> None:
    a, _ = two_companies
    first = svc.issue_code("uk@indigothread.example", "signin", company_id=a)
    clock.tick(seconds=30)
    with pytest.raises(auth.ResendTooSoon) as e:
        svc.issue_code("uk@indigothread.example", "signin", company_id=a)
    assert e.value.seconds_left == 15
    clock.tick(seconds=15)
    second = svc.issue_code("uk@indigothread.example", "signin", company_id=a)
    if first.code != second.code:
        with pytest.raises((auth.WrongCode, auth.NoCode)):
            svc.verify_code("uk@indigothread.example", "signin", first.code, company_id=a)
    svc.verify_code("uk@indigothread.example", "signin", second.code, company_id=a)


def test_hourly_cap_per_email(svc: auth.AuthService, clock: Clock, two_companies: Ids) -> None:
    a, _ = two_companies
    for _ in range(auth.MAX_CODES_PER_HOUR):
        svc.issue_code("uk@indigothread.example", "signin", company_id=a)
        clock.tick(seconds=46)
    with pytest.raises(auth.TooManyCodes):
        svc.issue_code("uk@indigothread.example", "signin", company_id=a)


def test_signup_code_carries_payload(svc: auth.AuthService) -> None:
    issued = svc.issue_code("new@acme.in", "signup", payload={"name": "A", "company": "Acme"})
    row = svc.verify_code("new@acme.in", "signup", issued.code)
    assert row.payload == {"name": "A", "company": "Acme"}


def _user(sf: sessionmaker[Session]) -> uuid.UUID:
    with sf() as s, system_scope("test"):
        return s.scalars(
            sa.select(AppUser.id).where(AppUser.email == "uk@indigothread.example")
        ).one()


def test_session_slides_and_expires(
    svc: auth.AuthService, clock: Clock, session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, _ = two_companies
    uid = _user(session_factory)
    token = svc.create_session(uid, a, "pytest")
    assert svc.session_for(token).company_id == a  # type: ignore[union-attr]
    clock.tick(days=29)
    assert svc.session_for(token) is not None  # renewed: 30 days from now
    clock.tick(days=29)
    assert svc.session_for(token) is not None
    clock.tick(days=31)
    assert svc.session_for(token) is None
    with session_factory() as s:
        assert token not in s.scalars(sa.select(AuthSession.token_hash)).one()


def test_sign_out_and_sign_out_everywhere(
    svc: auth.AuthService, session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, _ = two_companies
    uid = _user(session_factory)
    t1, t2, t3 = (svc.create_session(uid, a, "x") for _ in range(3))
    svc.revoke(t1)
    assert svc.session_for(t1) is None and svc.session_for(t2) is not None
    assert svc.revoke_all(uid) == 2
    assert svc.session_for(t2) is None and svc.session_for(t3) is None


def test_handoff_is_single_use_60_seconds_and_company_bound(
    svc: auth.AuthService, clock: Clock, session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, b = two_companies
    uid = _user(session_factory)
    h = svc.create_handoff(uid, a)
    assert svc.session_for(h) is None  # a handoff token is not a session
    with pytest.raises(auth.BadHandoff):
        svc.redeem_handoff(h, b)
    token = svc.redeem_handoff(h, a)
    assert svc.session_for(token) is not None
    with pytest.raises(auth.BadHandoff):
        svc.redeem_handoff(h, a)
    late = svc.create_handoff(uid, a)
    clock.tick(seconds=61)
    with pytest.raises(auth.BadHandoff):
        svc.redeem_handoff(late, a)


def test_csrf_double_submit() -> None:
    t = auth.new_csrf_token()
    assert auth.csrf_ok(t, t)
    assert not auth.csrf_ok(t, auth.new_csrf_token())
    assert not auth.csrf_ok(t, None) and not auth.csrf_ok(None, None)
