# SPDX-License-Identifier: Apache-2.0
"""Tenancy guard in the repository layer.

Every table with TenantMixin is readable and writable only inside `tenant_scope(company_id)`:
- SELECTs (including relationship loads and ORM bulk UPDATE/DELETE) get
  `company_id = <scope>` added automatically;
- a tenant query with no scope raises TenantScopeMissing;
- a write whose company_id is not the scope's raises CrossTenantWrite;
- `event` rows can never be updated or deleted (AppendOnlyViolation).

`system_scope(reason)` is the explicit escape hatch for the scheduler, CLI and migrations;
each use is logged. Use `Repo` rather than `Session.get` (the identity map can bypass SQL).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker, with_loader_criteria

if TYPE_CHECKING:
    from othor.core.models import IdMixin

log = logging.getLogger(__name__)


class TenantScopeMissing(RuntimeError):
    pass


class CrossTenantWrite(RuntimeError):
    pass


class AppendOnlyViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class TenantScope:
    company_id: uuid.UUID


@dataclass(frozen=True)
class SystemScope:
    reason: str


_scope: ContextVar[TenantScope | SystemScope | None] = ContextVar("othor_scope", default=None)


@contextmanager
def tenant_scope(company_id: uuid.UUID) -> Iterator[TenantScope]:
    current = _scope.get()
    if isinstance(current, TenantScope) and current.company_id != company_id:
        raise CrossTenantWrite("cannot enter another company's scope inside a company scope")
    token = _scope.set(TenantScope(company_id))
    try:
        yield TenantScope(company_id)
    finally:
        _scope.reset(token)


@contextmanager
def system_scope(reason: str) -> Iterator[SystemScope]:
    log.info("system scope entered", extra={"reason": reason})
    token = _scope.set(SystemScope(reason))
    try:
        yield SystemScope(reason)
    finally:
        _scope.reset(token)


def current_scope() -> TenantScope | SystemScope | None:
    return _scope.get()


def current_company_id() -> uuid.UUID:
    scope = _scope.get()
    if not isinstance(scope, TenantScope):
        raise TenantScopeMissing("this needs a company scope")
    return scope.company_id


def _is_tenant(cls: type) -> bool:
    from othor.core.models import TenantMixin

    return isinstance(cls, type) and issubclass(cls, TenantMixin)


def _is_event(cls: type) -> bool:
    """Append-only tables (event, spec): never updated or deleted."""
    from othor.core.models import AppendOnly

    return isinstance(cls, type) and issubclass(cls, AppendOnly)


def _append_only_name(classes: Any) -> str:
    names = sorted({getattr(c, "__tablename__", "row") for c in classes if _is_event(c)})
    return ", ".join(names) or "event"


def _on_execute(state: ORMExecuteState) -> None:
    from othor.core.models import Company, TenantMixin

    if not (state.is_select or state.is_update or state.is_delete):
        return
    mappers = [m.class_ for m in state.all_mappers]
    if (state.is_update or state.is_delete) and any(_is_event(c) for c in mappers):
        raise AppendOnlyViolation(f"{_append_only_name(mappers)} is append-only")
    scope = _scope.get()
    if isinstance(scope, SystemScope):
        return
    if scope is None:
        if any(_is_tenant(c) for c in mappers):
            raise TenantScopeMissing(
                "tenant table queried without a company scope: "
                + ", ".join(sorted(c.__name__ for c in mappers if _is_tenant(c)))
            )
        return
    cid = scope.company_id
    state.statement = state.statement.options(
        with_loader_criteria(TenantMixin, lambda cls: cls.company_id == cid, include_aliases=True),
        with_loader_criteria(Company, lambda cls: cls.id == cid, include_aliases=True),
    )


def _before_flush(session: Session, _ctx: Any, _instances: Any) -> None:
    from othor.core.models import Company

    scope = _scope.get()
    for obj in session.dirty | session.deleted:
        if _is_event(type(obj)):
            raise AppendOnlyViolation(f"{_append_only_name([type(obj)])} is append-only")
    for obj in session.deleted:
        if isinstance(obj, Company):
            raise CrossTenantWrite("companies are soft-deleted (set deleted_at), never deleted")
    touched = [o for o in (*session.new, *session.dirty, *session.deleted) if _is_tenant(type(o))]
    if isinstance(scope, SystemScope):
        for obj in session.new:
            if _is_tenant(type(obj)) and obj.workspace_id is None:
                obj.workspace_id = obj.company_id
        return
    if scope is None:
        if touched:
            raise TenantScopeMissing("tenant rows written without a company scope")
        return
    cid = scope.company_id
    for obj in touched:
        if obj in session.new and obj.company_id is None:
            obj.company_id = cid
        if obj.company_id != cid:
            raise CrossTenantWrite(f"{type(obj).__name__} belongs to another company")
        hist = sa.inspect(obj).attrs.company_id.history
        if hist.deleted and any(v not in (None, cid) for v in hist.deleted):
            raise CrossTenantWrite(f"{type(obj).__name__} belongs to another company")
        if obj.workspace_id is None:
            obj.workspace_id = obj.company_id
    for obj in (*session.new, *session.dirty):
        if isinstance(obj, Company) and obj.id not in (None, cid) and obj not in session.new:
            raise CrossTenantWrite("cannot change another company")


def install_guards(factory: sessionmaker[Session]) -> None:
    event.listen(factory, "do_orm_execute", _on_execute)
    event.listen(factory, "before_flush", _before_flush)


M = TypeVar("M", bound="IdMixin")


class Repo:
    """Scoped access to metadata rows. Returns None for another company's object."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, model: type[M], id_: uuid.UUID) -> M | None:
        stmt = sa.select(model).where(model.id == id_).execution_options(populate_existing=True)
        return self.session.scalars(stmt).one_or_none()

    def list(self, model: type[M], *where: sa.ColumnElement[bool]) -> Sequence[M]:
        return self.session.scalars(sa.select(model).where(*where)).all()

    def add(self, obj: M) -> M:
        self.session.add(obj)
        return obj
