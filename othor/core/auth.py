# SPDX-License-Identifier: Apache-2.0
"""Email codes, sessions and the signup handoff (docs/01 §2). Web-free; the routes call this.

- Codes: 6 digits from `secrets`, stored as HMAC-SHA256 under a per-instance subkey and bound
  to (email, purpose, company). 10 minutes, 5 attempts, resend after 45 s, 15-minute lock after
  too many attempts, at most MAX_CODES_PER_HOUR per email and purpose.
- Sessions: 32 random bytes in the cookie, SHA-256 in the table, 30 days sliding.
- Handoff: signup happens on the root host; a one-use token valid for 60 s moves the new owner
  to the company host, where it becomes a session. No cross-subdomain cookies.
- CSRF: double submit (cookie and form field/header), compared in constant time.
- Invitations (M11d part C): 128 random bits, URL-safe; only the SHA-256 is stored. Single use,
  bound to one email (case-insensitive), 14 days by default; used in the same transaction that
  creates the company. Requests for access: one per email per day.

Time comes from an injected clock so tests can move it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core.crypto import InstanceKey
from othor.core.events import record_platform_event
from othor.core.models import AccessRequest, AuthSession, Company, Invitation, LoginCode, utcnow

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_AFTER = timedelta(seconds=45)
LOCK_FOR = timedelta(minutes=15)
MAX_CODES_PER_HOUR = 10
SESSION_TTL = timedelta(days=30)
HANDOFF_TTL = timedelta(seconds=60)
# `make demo`'s one-time sign-in link (owner, 25 Nov 2026): a handoff that lives 15 minutes. Only
# `othor demo` makes one (it refuses outside OTHOR_ENV=dev) and only /auth/demo takes one (404
# outside dev); /auth/handoff refuses it, so a long link can't sign anyone in on a production host.
DEMO_LINK_TTL = timedelta(minutes=15)
NO_COMPANY = uuid.UUID(int=0)
INVITE_TTL = timedelta(days=14)
SYSTEM = "system"
OPERATOR = "operator"  # the actor of `othor admin invite` (the command line on the server)


def last_seen_by_company(s: Session) -> list[tuple[uuid.UUID, datetime | None]]:
    """When each company was last seen signed in (operator reports; read-only)."""
    rows = s.execute(
        sa.select(AuthSession.company_id, sa.func.max(AuthSession.last_seen_at)).group_by(
            AuthSession.company_id
        )
    )
    return [(r[0], r[1]) for r in rows]


class AuthError(Exception):
    pass


class NoCode(AuthError):
    """No live code for this email/purpose/company (never sent, used, or replaced)."""


class Expired(AuthError):
    pass


@dataclass
class WrongCode(AuthError):
    attempts_left: int


@dataclass
class Locked(AuthError):
    until: datetime


@dataclass
class ResendTooSoon(AuthError):
    seconds_left: int


class TooManyCodes(AuthError):
    pass


class BadHandoff(AuthError):
    pass


class BadInvitation(AuthError):
    """Unknown, used, expired or revoked. The page never says which (M11d part C)."""


class WrongInviteEmail(AuthError):
    """Signing up with an address other than the one invited."""


@dataclass(frozen=True)
class InvitationInfo:
    id: uuid.UUID
    email: str
    company_name: str | None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None

    def status(self, now: datetime) -> str:
        if self.revoked_at:
            return "revoked"
        if self.used_at:
            return "used"
        return "expired" if self.expires_at <= now else "open"


def _info(row: Invitation) -> InvitationInfo:
    return InvitationInfo(
        row.id,
        row.email,
        row.company_name,
        row.created_at,
        row.expires_at,
        row.used_at,
        row.revoked_at,
    )


@dataclass(frozen=True)
class IssuedCode:
    code: str
    resend_after: datetime
    expires_at: datetime


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(cookie: str | None, submitted: str | None) -> bool:
    return bool(cookie and submitted) and hmac.compare_digest(str(cookie), str(submitted))


class AuthService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        key: InstanceKey,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.sf = session_factory
        self.key = key
        self.now = clock

    # codes ---------------------------------------------------------------------------------

    def _hash(self, email: str, purpose: str, company_id: uuid.UUID | None, code: str) -> str:
        k = self.key.subkey(company_id or NO_COMPANY, "login-code")
        return hmac.new(k, f"{email}|{purpose}|{code}".encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _same(
        email: str, purpose: str, company_id: uuid.UUID | None
    ) -> list[sa.ColumnElement[bool]]:
        where = [LoginCode.email == email, LoginCode.purpose == purpose]
        where.append(
            LoginCode.company_id.is_(None)
            if company_id is None
            else LoginCode.company_id == company_id
        )
        return where

    def issue_code(
        self,
        email: str,
        purpose: str,
        *,
        company_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> IssuedCode:
        email = email.strip().lower()
        now = self.now()
        with self.sf() as s, s.begin():
            rows = s.scalars(
                sa.select(LoginCode)
                .where(*self._same(email, purpose, company_id))
                .where(LoginCode.created_at > now - timedelta(hours=1))
            ).all()
            locked = [r.locked_until for r in rows if r.locked_until and r.locked_until > now]
            if locked:
                raise Locked(max(locked))
            live = [r for r in rows if r.used_at is None and r.expires_at > now]
            for r in live:
                if r.resend_after > now:
                    raise ResendTooSoon(int((r.resend_after - now).total_seconds() + 0.999))
            if len(rows) >= MAX_CODES_PER_HOUR:
                raise TooManyCodes()
            for r in live:
                r.used_at = now  # a new code replaces the old one
            code = f"{secrets.randbelow(1_000_000):06d}"
            s.add(
                LoginCode(
                    company_id=company_id,
                    workspace_id=company_id,
                    email=email,
                    purpose=purpose,
                    code_hash=self._hash(email, purpose, company_id, code),
                    payload=payload,
                    expires_at=now + CODE_TTL,
                    attempts=0,
                    resend_after=now + RESEND_AFTER,
                    created_at=now,
                )
            )
        return IssuedCode(code, now + RESEND_AFTER, now + CODE_TTL)

    def resend_in(self, email: str, purpose: str, company_id: uuid.UUID | None = None) -> int:
        """Seconds until a new code may be sent (0 = now). For the 'Resend code (0:42)' link."""
        now = self.now()
        with self.sf() as s:
            r = s.scalars(
                sa.select(LoginCode)
                .where(*self._same(email.strip().lower(), purpose, company_id))
                .where(LoginCode.used_at.is_(None))
                .order_by(LoginCode.created_at.desc())
            ).first()
        if r is None or r.resend_after <= now:
            return 0
        return int((r.resend_after - now).total_seconds() + 0.999)

    def verify_code(
        self, email: str, purpose: str, code: str, *, company_id: uuid.UUID | None = None
    ) -> LoginCode:
        email = email.strip().lower()
        code = "".join(ch for ch in code if ch.isdigit())
        now = self.now()
        failure: AuthError | None = None
        with self.sf() as s, s.begin():  # failures are committed (attempts, lock), then raised
            rows = s.scalars(
                sa.select(LoginCode)
                .where(*self._same(email, purpose, company_id))
                .order_by(LoginCode.created_at.desc())
            ).all()
            locked = [r.locked_until for r in rows if r.locked_until and r.locked_until > now]
            row = next((r for r in rows if r.used_at is None), None)
            if locked:
                failure = Locked(max(locked))
            elif row is None:
                failure = NoCode()
            elif row.expires_at <= now:
                failure = Expired()
            elif not hmac.compare_digest(
                row.code_hash, self._hash(email, purpose, company_id, code)
            ):
                row.attempts += 1
                if row.attempts >= MAX_ATTEMPTS:
                    row.locked_until = now + LOCK_FOR
                    row.used_at = now
                    failure = Locked(row.locked_until)
                else:
                    failure = WrongCode(MAX_ATTEMPTS - row.attempts)
            else:
                row.used_at = now
                s.flush()
                s.expunge(row)
        if failure is not None:
            raise failure
        assert row is not None
        return row

    # sessions ------------------------------------------------------------------------------

    def create_session(
        self, user_id: uuid.UUID, company_id: uuid.UUID, user_agent: str | None
    ) -> str:
        token = secrets.token_urlsafe(32)
        now = self.now()
        with self.sf() as s, s.begin():
            s.add(
                AuthSession(
                    company_id=company_id,
                    workspace_id=company_id,
                    user_id=user_id,
                    token_hash=_sha(token),
                    expires_at=now + SESSION_TTL,
                    last_seen_at=now,
                    created_at=now,
                    user_agent=(user_agent or "")[:300],
                )
            )
        return token

    def session_for(self, token: str | None) -> AuthSession | None:
        """The live session for a cookie token, renewed for another 30 days; else None."""
        if not token:
            return None
        now = self.now()
        with self.sf() as s, s.begin():
            row = s.scalars(
                sa.select(AuthSession).where(AuthSession.token_hash == _sha(token))
            ).first()
            if row is None or row.revoked_at or row.expires_at <= now or row.handoff_hash:
                return None
            if _closed(s, row.company_id):  # a closed company signs no one in (M18.2)
                return None
            row.expires_at = now + SESSION_TTL
            row.last_seen_at = now
            s.flush()
            s.expunge(row)
            return row

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self.sf() as s, s.begin():
            s.execute(
                sa.update(AuthSession)
                .where(AuthSession.token_hash == _sha(token))
                .values(revoked_at=self.now())
            )

    def live_sessions(self, user_id: uuid.UUID, company_id: uuid.UUID) -> list[AuthSession]:
        """One user's live sessions on one company, most recently active first: the Account
        page's list (M15.8). Not renewed; nothing here changes them."""
        now = self.now()
        with self.sf() as s:
            rows = s.scalars(
                sa.select(AuthSession)
                .where(
                    AuthSession.user_id == user_id,
                    AuthSession.company_id == company_id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > now,
                    AuthSession.handoff_hash.is_(None),
                )
                .order_by(AuthSession.last_seen_at.desc(), AuthSession.created_at.desc())
            ).all()
            s.expunge_all()
            return list(rows)

    def end_company(self, company_id: uuid.UUID) -> int:
        """Closing a company (M18.2): every live session on it ends and every unused code on it
        is spent. Returns the sessions ended."""
        now = self.now()
        with self.sf() as s, s.begin():
            res = s.execute(
                sa.update(AuthSession)
                .where(AuthSession.company_id == company_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            s.execute(
                sa.update(LoginCode)
                .where(LoginCode.company_id == company_id, LoginCode.used_at.is_(None))
                .values(used_at=now)
            )
            return int(res.rowcount or 0)  # type: ignore[attr-defined]

    def end_member(self, user_id: uuid.UUID, company_id: uuid.UUID) -> int:
        """A person removed from a company (0.34): their sessions on it end at once; their
        sessions on other companies stay. Returns the sessions ended."""
        with self.sf() as s, s.begin():
            res = s.execute(
                sa.update(AuthSession)
                .where(
                    AuthSession.user_id == user_id,
                    AuthSession.company_id == company_id,
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=self.now())
            )
            return int(res.rowcount or 0)  # type: ignore[attr-defined]

    def revoke_all(self, user_id: uuid.UUID) -> int:
        with self.sf() as s, s.begin():
            res = s.execute(
                sa.update(AuthSession)
                .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=self.now())
            )
            return int(res.rowcount or 0)  # type: ignore[attr-defined]

    # handoff -------------------------------------------------------------------------------

    def create_handoff(
        self, user_id: uuid.UUID, company_id: uuid.UUID, *, ttl: timedelta = HANDOFF_TTL
    ) -> str:
        if ttl not in (HANDOFF_TTL, DEMO_LINK_TTL):
            raise ValueError("a handoff lives 60 seconds, or 15 minutes for make demo's link")
        handoff = secrets.token_urlsafe(32)
        now = self.now()
        with self.sf() as s, s.begin():
            s.add(
                AuthSession(
                    company_id=company_id,
                    workspace_id=company_id,
                    user_id=user_id,
                    token_hash=None,
                    handoff_hash=_sha(handoff),
                    handoff_expires_at=now + ttl,
                    expires_at=now + ttl,
                    last_seen_at=now,
                    created_at=now,
                )
            )
        return handoff

    def redeem_handoff(
        self,
        handoff: str,
        company_id: uuid.UUID,
        user_agent: str | None = None,
        *,
        demo: bool = False,
    ) -> str:
        """Once. `demo`: only make demo's 15-minute link (the /auth/demo route, dev only);
        otherwise only the 60-second signup handoff, so neither route takes the other's."""
        now = self.now()
        with self.sf() as s, s.begin():
            row = s.scalars(
                sa.select(AuthSession).where(AuthSession.handoff_hash == _sha(handoff))
            ).first()
            if (
                row is None
                or row.company_id != company_id
                or row.revoked_at
                or not row.handoff_expires_at
                or row.handoff_expires_at <= now
                or (row.handoff_expires_at - row.created_at > HANDOFF_TTL) != demo
            ):
                raise BadHandoff()
            token = secrets.token_urlsafe(32)
            row.handoff_hash = None
            row.handoff_expires_at = None
            row.token_hash = _sha(token)
            row.expires_at = now + SESSION_TTL
            row.last_seen_at = now
            if user_agent:  # the device, for the Account page's session list (M15.8)
                row.user_agent = user_agent[:300]
            return token

    # invitations and requests for access (M11d part C) ---------------------------------------

    def create_invitation(
        self, email: str, *, days: int = INVITE_TTL.days, company_name: str | None = None
    ) -> tuple[InvitationInfo, str]:
        """A new invitation; returns it and the plain code, which is shown once and never stored."""
        email = email.strip().lower()
        if "@" not in email or days < 1:
            raise ValueError("an email address and at least one day")
        code = secrets.token_urlsafe(16)  # 128 bits
        now = self.now()
        with self.sf() as s, s.begin():
            row = Invitation(
                code_hash=_sha(code),
                email=email,
                company_name=(company_name or "").strip() or None,
                expires_at=now + timedelta(days=days),
                created_at=now,
            )
            s.add(row)
            s.flush()
            record_platform_event(
                s,
                actor=OPERATOR,
                action="invitation.created",
                object_type="invitation",
                object_id=row.id,
                after={"email_domain": email.split("@")[-1], "days": days},
                at=now,
            )
            return _info(row), code

    def invitations(self) -> list[InvitationInfo]:
        with self.sf() as s:
            rows = s.scalars(sa.select(Invitation).order_by(Invitation.created_at.desc()))
            return [_info(r) for r in rows]

    def revoke_invitation(self, invitation_id: uuid.UUID) -> InvitationInfo | None:
        now = self.now()
        with self.sf() as s, s.begin():
            row = s.scalars(sa.select(Invitation).where(Invitation.id == invitation_id)).first()
            if row is None:
                return None
            if row.revoked_at is None and row.used_at is None:
                row.revoked_at = now
                record_platform_event(
                    s,
                    actor=OPERATOR,
                    action="invitation.revoked",
                    object_type="invitation",
                    object_id=row.id,
                    at=now,
                )
            return _info(row)

    def _usable(self, s: Session, code: str, now: datetime) -> Invitation:
        row = s.scalars(
            sa.select(Invitation).where(Invitation.code_hash == _sha(code or ""))
        ).first()
        if row is None or row.used_at or row.revoked_at:
            raise BadInvitation()
        if row.expires_at <= now:
            record_platform_event(
                s,
                actor=SYSTEM,
                action="invitation.expired_on_use",
                object_type="invitation",
                object_id=row.id,
                at=now,
            )
            raise BadInvitation()
        return row

    def check_invitation(self, code: str, email: str | None = None) -> InvitationInfo:
        """The invitation for `code` if it can still be used (and, given an email, if it's the
        invited one); raises BadInvitation or WrongInviteEmail. Writes only the expired event."""
        now = self.now()
        with self.sf() as s, s.begin():
            row = self._usable(s, code, now)
            if email is not None and row.email != email.strip().lower():
                raise WrongInviteEmail()
            return _info(row)

    def use_invitation(self, s: Session, code: str, email: str, company_id: uuid.UUID) -> None:
        """Mark the invitation used by `company_id`, in the caller's transaction (the one that
        creates the company), so a second use fails and a failed sign-up leaves it open."""
        now = self.now()
        row = self._usable(s, code, now)
        if row.email != email.strip().lower():
            raise WrongInviteEmail()
        row.used_at = now
        row.used_by_company = company_id
        record_platform_event(
            s,
            actor=SYSTEM,
            action="invitation.used",
            object_type="invitation",
            object_id=row.id,
            after={"company_id": str(company_id)},
            at=now,
        )

    def request_access(
        self,
        name: str,
        email: str,
        company: str,
        message: str | None,
        team_size: str | None = None,
        runs_today: str | None = None,
    ) -> bool:
        """Request access (M11d part C): now a Contact us message on the sales topic."""
        return self.contact(
            name, email, company, message, "sales", team_size=team_size, runs_today=runs_today
        )

    def contact(
        self,
        name: str,
        email: str,
        company: str,
        message: str | None,
        topic: str,
        *,
        team_size: str | None = None,
        runs_today: str | None = None,
        company_slug: str | None = None,
    ) -> bool:
        """Store a Contact us message (owner, 17 Nov 2026). True when it's new; a second one from
        the same email on the same topic and (UTC) day is the same message, so nothing is stored
        or sent twice."""
        email = email.strip().lower()
        now = self.now()
        with self.sf() as s:
            row = AccessRequest(
                name=name.strip()[:200],
                email=email,
                company=company.strip()[:200],
                team_size=(team_size or "").strip()[:32] or None,
                runs_today=(runs_today or "").strip()[:2000] or None,
                message=(message or "").strip()[:2000] or None,
                topic=topic[:32],
                company_slug=company_slug,
                day=now.date().isoformat(),
                created_at=now,
            )
            s.add(row)
            try:
                s.flush()
            except sa.exc.IntegrityError:
                s.rollback()
                return False
            record_platform_event(
                s,
                actor=SYSTEM,
                action="contact.sent",
                object_type="access_request",
                object_id=row.id,
                after={
                    "email_domain": email.split("@")[-1],
                    "topic": topic,
                    "team_size": team_size or None,
                    "company": company_slug,
                },
                at=now,
            )
            s.commit()
            return True


def _closed(s: Session, company_id: uuid.UUID | None) -> bool:
    if company_id is None:
        return False
    # company isn't a tenant table: readable with no scope (a session is read before one)
    got = s.scalar(sa.select(Company.deleted_at).where(Company.id == company_id))
    return got is not None


def purge_rows(s: Session, company_id: uuid.UUID, gone: dict[uuid.UUID, str]) -> int:
    """The purge (M18.3), in its transaction: every session and code on the company, and the
    sessions and codes of the users (`gone`: id -> email) whose last membership it was."""
    n = s.execute(sa.delete(AuthSession).where(AuthSession.company_id == company_id)).rowcount  # type: ignore[attr-defined]
    n += s.execute(sa.delete(LoginCode).where(LoginCode.company_id == company_id)).rowcount  # type: ignore[attr-defined]
    if gone:
        n += s.execute(sa.delete(AuthSession).where(AuthSession.user_id.in_(list(gone)))).rowcount  # type: ignore[attr-defined]
        n += s.execute(
            sa.delete(LoginCode).where(LoginCode.email.in_(list(gone.values())))
        ).rowcount  # type: ignore[attr-defined]
    return int(n)
