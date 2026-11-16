# SPDX-License-Identifier: Apache-2.0
"""Guard 5 (docs/11): company A's scope can never read or write company B's objects.

This is the M0 acceptance test. The web layer maps `Repo.get -> None` to 404 in M1.
"""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core.events import record_event
from othor.core.models import Company, Event, Membership
from othor.core.tenancy import (
    CrossTenantWrite,
    Repo,
    TenantScopeMissing,
    system_scope,
    tenant_scope,
)

Ids = tuple[uuid.UUID, uuid.UUID]


def _seed_events(sf: sessionmaker[Session], a: uuid.UUID, b: uuid.UUID) -> dict[str, uuid.UUID]:
    ids = {}
    for name, cid in (("a", a), ("b", b)):
        with sf() as s, tenant_scope(cid):
            ids[name] = record_event(
                s, actor="system", action="seed", object_type="t", object_id=name
            ).id
            s.commit()
    return ids


def test_get_other_company_object_returns_none(
    session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, b = two_companies
    ids = _seed_events(session_factory, a, b)
    with session_factory() as s, tenant_scope(a):
        repo = Repo(s)
        assert repo.get(Event, ids["a"]) is not None
        assert repo.get(Event, ids["b"]) is None
        assert repo.get(Company, b) is None
        assert repo.get(Company, a) is not None


def test_lists_and_raw_orm_selects_are_filtered(
    session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, b = two_companies
    _seed_events(session_factory, a, b)
    with session_factory() as s, tenant_scope(a):
        assert {e.company_id for e in Repo(s).list(Event)} == {a}
        assert {m.company_id for m in s.scalars(sa.select(Membership))} == {a}
        assert s.scalar(sa.select(sa.func.count()).select_from(Event)) == 1
        assert [c.id for c in s.scalars(sa.select(Company))] == [a]
        # An explicit filter for B's id still returns nothing.
        assert s.scalars(sa.select(Event).where(Event.company_id == b)).all() == []


def test_relationship_loads_are_filtered(
    session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, _ = two_companies
    with session_factory() as s, tenant_scope(a):
        company = Repo(s).get(Company, a)
        assert company is not None
        assert {m.company_id for m in company.memberships} == {a}


def test_write_for_other_company_refused(
    session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, b = two_companies
    with session_factory() as s, tenant_scope(a):
        s.add(Event(company_id=b, actor="system", action="x", object_type="t", object_id="1"))
        with pytest.raises(CrossTenantWrite):
            s.flush()


def test_moving_a_row_to_another_company_refused(
    session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, b = two_companies
    with session_factory() as s, tenant_scope(a):
        m = Repo(s).list(Membership)[0]
        m.company_id = b
        with pytest.raises(CrossTenantWrite):
            s.flush()


def test_bulk_update_is_scoped(session_factory: sessionmaker[Session], two_companies: Ids) -> None:
    a, b = two_companies
    with session_factory() as s, tenant_scope(a):
        s.execute(sa.update(Membership).values(role="owner2"))
        s.commit()
    with session_factory() as s, system_scope("assert"):
        roles = {m.company_id: m.role for m in s.scalars(sa.select(Membership))}
    assert roles == {a: "owner2", b: "owner"}


def test_no_scope_means_no_tenant_reads_or_writes(
    session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, _ = two_companies
    with session_factory() as s:
        with pytest.raises(TenantScopeMissing):
            s.scalars(sa.select(Event)).all()
        with pytest.raises(TenantScopeMissing):
            Repo(s).list(Membership)
        s.add(Event(company_id=a, actor="system", action="x", object_type="t", object_id="1"))
        with pytest.raises(TenantScopeMissing):
            s.flush()


def test_scopes_do_not_nest_across_companies(two_companies: Ids) -> None:
    a, b = two_companies
    with tenant_scope(a), pytest.raises(CrossTenantWrite), tenant_scope(b):
        pass
