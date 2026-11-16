# SPDX-License-Identifier: Apache-2.0
"""Core metadata tables: company, app_user, membership, event (docs/03)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship

from othor.core.naming import NAMING_CONVENTION


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(sa.TypeDecorator[datetime]):
    """Timezone-aware UTC on every dialect (SQLite stores naive values; we re-attach UTC)."""

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: use timezone-aware UTC")
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: sa.JSON,
        datetime: UtcDateTime(),
    }


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)


class TenantMixin:
    """Every tenant table carries company_id and workspace_id (= company for now).

    Reads and writes are scoped by othor.core.tenancy; never query these tables unscoped.
    """

    @declared_attr
    @classmethod
    def company_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(sa.Uuid, sa.ForeignKey("company.id"), nullable=False, index=True)

    workspace_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)


class AppendOnly:
    """Rows of these tables are never updated or deleted (ORM guard in othor.core.tenancy and a
    database trigger in their migration): event, spec."""

    __append_only__ = True


class Company(IdMixin, Base):
    """The tenant root: its id is the company_id of every tenant row."""

    __tablename__ = "company"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    slug: Mapped[str] = mapped_column(sa.String(63), unique=True)
    name: Mapped[str] = mapped_column(sa.String(200))
    website: Mapped[str | None] = mapped_column(sa.String(300))
    fiscal_year_start_month: Mapped[int] = mapped_column(sa.SmallInteger, default=4)
    currency: Mapped[str] = mapped_column(sa.String(3), default="INR")
    number_format: Mapped[str] = mapped_column(sa.String(16), default="indian")
    timezone: Mapped[str] = mapped_column(sa.String(64), default="Asia/Kolkata")
    run_time: Mapped[str] = mapped_column(sa.String(5), default="02:00")
    brief_time: Mapped[str] = mapped_column(sa.String(5), default="06:00")
    paused: Mapped[bool] = mapped_column(default=False)
    email_delivery: Mapped[bool] = mapped_column(default=True)
    weekly_digest: Mapped[bool] = mapped_column(default=False)  # 8e, Sundays, owner only (M9)
    # Big Picture's language (v0.19, M19.5): en · hi (a try)
    report_language: Mapped[str] = mapped_column(sa.String(8), default="en")
    # the company's logo (M15.7): a PNG we re-encoded, stored as companies/<id>/logo/<sha>.png
    logo_sha: Mapped[str | None] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column()
    # closed by the owner (M18.2): purged after this, unless support reopens it before
    purge_after: Mapped[datetime | None] = mapped_column()
    closed_by: Mapped[str | None] = mapped_column(sa.String(64))
    purged_at: Mapped[datetime | None] = mapped_column()  # M18.3: scrubbed to a placeholder

    memberships: Mapped[list[Membership]] = relationship(back_populates="company")


class AppUser(IdMixin, Base):
    """Global identity (an email). `user` is reserved in Postgres, hence app_user."""

    __tablename__ = "app_user"

    email: Mapped[str] = mapped_column(sa.String(320), unique=True)
    name: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    @sa.orm.validates("email")
    def _lower(self, _key: str, value: str) -> str:
        return value.strip().lower()


class Membership(IdMixin, TenantMixin, Base):
    __tablename__ = "membership"
    __table_args__ = (sa.UniqueConstraint("company_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("app_user.id"))
    role: Mapped[str] = mapped_column(sa.String(32), default="owner")

    company: Mapped[Company] = relationship(back_populates="memberships")


class Event(AppendOnly, IdMixin, TenantMixin, Base):
    """Append-only log of every owner action and system event. Never updated or deleted."""

    __tablename__ = "event"
    __table_args__ = (
        sa.Index("ix_event_company_at", "company_id", "at"),
        sa.Index("ix_event_company_object", "company_id", "object_type", "object_id"),
    )

    actor: Mapped[str] = mapped_column(sa.String(64))  # user id or 'system'
    action: Mapped[str] = mapped_column(sa.String(100))
    object_type: Mapped[str] = mapped_column(sa.String(64))
    object_id: Mapped[str] = mapped_column(sa.String(100))
    before: Mapped[dict[str, Any] | None] = mapped_column()
    after: Mapped[dict[str, Any] | None] = mapped_column()
    at: Mapped[datetime] = mapped_column(default=utcnow)


class LoginCode(IdMixin, Base):
    """A 6-digit email code (sign-in or signup). Stored as an HMAC, never in clear.

    Not a TenantMixin table: it's read before a company scope exists (signup has none).
    Only othor.core.auth touches it.
    """

    __tablename__ = "login_code"
    __table_args__ = (sa.Index("ix_login_code_lookup", "email", "purpose", "company_id"),)

    company_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("company.id"))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    email: Mapped[str] = mapped_column(sa.String(320))
    purpose: Mapped[str] = mapped_column(sa.String(16))  # signin | signup | close
    code_hash: Mapped[str] = mapped_column(sa.String(64))
    payload: Mapped[dict[str, Any] | None] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column()
    attempts: Mapped[int] = mapped_column(default=0)
    resend_after: Mapped[datetime] = mapped_column()
    locked_until: Mapped[datetime | None] = mapped_column()
    used_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuthSession(IdMixin, Base):
    """A signed-in browser session (cookie token stored as SHA-256). Also carries the one-use
    signup handoff from the root host to the company host.

    Not a TenantMixin table: it's how a request finds its company. Only othor.core.auth uses it.
    """

    __tablename__ = "auth_session"

    company_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("company.id"), index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("app_user.id"), index=True)
    token_hash: Mapped[str | None] = mapped_column(sa.String(64), unique=True)
    handoff_hash: Mapped[str | None] = mapped_column(sa.String(64), unique=True)
    handoff_expires_at: Mapped[datetime | None] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column()
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    user_agent: Mapped[str | None] = mapped_column(sa.String(300))


class Invitation(IdMixin, Base):
    """A pre-approved sign-up link (M11d part C): `othor.ai/signup?invite=<code>`. The code is
    128 random bits; only its SHA-256 is stored. Single use, bound to one email, expires.

    Not a TenantMixin table: it exists before its company. Only othor.core.auth touches it.
    """

    __tablename__ = "invitation"

    code_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    email: Mapped[str] = mapped_column(sa.String(320))
    company_name: Mapped[str | None] = mapped_column(sa.String(200))
    expires_at: Mapped[datetime] = mapped_column()
    used_at: Mapped[datetime | None] = mapped_column()
    used_by_company: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("company.id"))
    revoked_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AccessRequest(IdMixin, Base):
    """A Request access form (M11d part C): kept 12 months, deleted on request. One per email per
    day (a double submit is the same request). Only othor.core.auth touches it."""

    __tablename__ = "access_request"
    __table_args__ = (sa.UniqueConstraint("email", "day"),)

    name: Mapped[str] = mapped_column(sa.String(200))
    email: Mapped[str] = mapped_column(sa.String(320))
    company: Mapped[str] = mapped_column(sa.String(200))
    team_size: Mapped[str | None] = mapped_column(
        sa.String(32)
    )  # a range (site_content.TEAM_SIZES)
    runs_today: Mapped[str | None] = mapped_column(sa.Text)  # the systems they run, in their words
    message: Mapped[str | None] = mapped_column(sa.Text)  # "Anything else"
    day: Mapped[str] = mapped_column(sa.String(10))  # UTC date, for the once-a-day rule
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PlatformEvent(AppendOnly, IdMixin, Base):
    """Append-only log of instance-level events that belong to no company yet (M11d part C):
    invitations created, used, revoked or refused as expired, and requests for access. Same
    shape as `event`, without the company."""

    __tablename__ = "platform_event"
    __table_args__ = (sa.Index("ix_platform_event_at", "at"),)

    actor: Mapped[str] = mapped_column(sa.String(64))  # 'operator', 'system' or a user id
    action: Mapped[str] = mapped_column(sa.String(100))
    object_type: Mapped[str] = mapped_column(sa.String(64))
    object_id: Mapped[str] = mapped_column(sa.String(100))
    before: Mapped[dict[str, Any] | None] = mapped_column()
    after: Mapped[dict[str, Any] | None] = mapped_column()
    at: Mapped[datetime] = mapped_column(default=utcnow)


class PurgeLock(Base):
    """M18.3: while a row exists for a company, the append-only triggers on `event` and `spec`
    let its rows be deleted (never updated). It exists only inside the purge's transaction."""

    __tablename__ = "purge_lock"

    company_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("company.id"), primary_key=True)
    started_at: Mapped[datetime] = mapped_column()


class SetupState(IdMixin, TenantMixin, Base):
    """Where the owner is in onboarding; signing in again resumes here (docs/01 §3)."""

    __tablename__ = "setup_state"
    __table_args__ = (sa.UniqueConstraint("company_id"),)

    current_step: Mapped[str] = mapped_column(sa.String(32), default="company")
    step_data: Mapped[dict[str, Any] | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class SummaryLayout(IdMixin, TenantMixin, Base):
    """The owner's Summary page (v0.16, M16.2): area order, hidden areas, the lead number they
    picked per area, and the view they last chose (window, comparison, % or absolute, split)."""

    __tablename__ = "summary_layout"
    __table_args__ = (sa.UniqueConstraint("company_id"),)

    order: Mapped[list[str] | None] = mapped_column(sa.JSON)
    hidden: Mapped[list[str] | None] = mapped_column(sa.JSON)
    leads: Mapped[dict[str, Any] | None] = mapped_column()
    view: Mapped[dict[str, Any] | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
