# SPDX-License-Identifier: Apache-2.0
"""M18.3: the append-only triggers let `event` and `spec` rows be deleted only for a company
holding a `purge_lock` row (inside the purge's transaction); UPDATE always refused, and every
other company untouched. On SQLite and PostgreSQL."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor import purging
from othor.core.events import record_event
from othor.core.models import AppUser, Base, Company, PlatformEvent, PurgeLock
from othor.core.tenancy import system_scope, tenant_scope

EVENT = Base.metadata.tables["event"]
NOW = dt.datetime(2026, 12, 20, tzinfo=dt.UTC)


def _events(sf, a, b):
    for cid in (a, b):
        with sf() as s, tenant_scope(cid):
            for i in range(3):
                record_event(s, actor="system", action="seed", object_type="t", object_id=str(i))
            s.commit()


def _count(sf, cid) -> int:
    with sf() as s, system_scope("test"):
        return s.scalar(
            sa.select(sa.func.count()).select_from(EVENT).where(EVENT.c.company_id == cid)
        )


def test_no_lock_no_delete_and_never_an_update(
    session_factory: sessionmaker[Session], two_companies: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_companies
    _events(session_factory, a, b)
    with session_factory() as s, system_scope("test: no lock"):
        with pytest.raises(sa.exc.DatabaseError):
            s.execute(sa.delete(EVENT).where(EVENT.c.company_id == a))
        s.rollback()
    with session_factory() as s, system_scope("test: a's lock, b's rows"):
        s.add(PurgeLock(company_id=a, started_at=NOW))
        s.flush()
        with pytest.raises(sa.exc.DatabaseError):
            s.execute(sa.delete(EVENT).where(EVENT.c.company_id == b))
        s.rollback()
    with session_factory() as s, system_scope("test: a's lock, an update"):
        s.add(PurgeLock(company_id=a, started_at=NOW))
        s.flush()
        with pytest.raises(sa.exc.DatabaseError):
            s.execute(sa.update(EVENT).where(EVENT.c.company_id == a).values(action="x"))
        s.rollback()
    assert _count(session_factory, a) == 3 and _count(session_factory, b) == 3


def test_the_purge_takes_one_company_and_leaves_the_other(
    session_factory: sessionmaker[Session], two_companies: tuple[uuid.UUID, uuid.UUID], tmp_path
) -> None:
    from othor.core.config import Settings

    a, b = two_companies
    _events(session_factory, a, b)
    settings = Settings(data_dir=tmp_path)
    folder = settings.company_dir(a)
    folder.mkdir(parents=True)
    (folder / "analytics.duckdb").write_bytes(b"x" * 10)
    with session_factory() as s, system_scope("test: close a"):
        c = s.scalars(sa.select(Company).where(Company.id == a)).one()
        c.deleted_at, c.purge_after = NOW - dt.timedelta(days=30), NOW
        s.commit()
    assert purging.tick(session_factory, settings, _Relay(), NOW - dt.timedelta(days=8)) == 0
    relay = _Relay()
    for _ in range(3):  # the reminder goes once, however many ticks
        assert purging.tick(session_factory, settings, relay, NOW - dt.timedelta(days=6)) == 0
    assert [m.subject for m in relay.sent] == [
        "[Othor AI] Indigo Thread Co. is removed for good on 20 Dec 2026"
    ]
    assert _count(session_factory, a) == 3
    assert purging.tick(session_factory, settings, relay, NOW) == 1
    assert _count(session_factory, a) == 0 and _count(session_factory, b) == 3
    assert not folder.exists()
    with session_factory() as s, system_scope("test: after"):
        c = s.scalars(sa.select(Company).where(Company.id == a)).one()
        assert c.slug == f"deleted-{a.hex[:8]}" and c.name == "Deleted company"
        assert c.purged_at is not None
        for t in purging.tenant_tables():
            n = s.scalar(sa.select(sa.func.count()).select_from(t).where(t.c.company_id == a))
            assert n == 0, t.name
        emails = set(s.scalars(sa.select(AppUser.email)))
        assert "uk@indigothread.example" not in emails and "owner@otherco.in" in emails
        pe = s.scalars(
            sa.select(PlatformEvent).where(PlatformEvent.action == "company.purged")
        ).one()
        assert pe.after["slug"] == "indigothread" and pe.after["tables"]["event"] == 3
        assert pe.after["files_bytes"] == 10 and pe.after["users"] == 1
        assert s.scalars(sa.select(PurgeLock)).all() == []
        # the address is free again
        s.add(Company(slug="indigothread", name="Indigo Thread Co."))
        s.commit()
    assert purging.tick(session_factory, settings, relay, NOW + dt.timedelta(days=1)) == 0


def test_every_table_is_purged_or_named():
    import othor.all_models  # noqa: F401

    tenant = {t.name for t in purging.tenant_tables()}
    for t in Base.metadata.tables.values():
        assert t.name in tenant or t.name in purging.GLOBAL, f"{t.name}: purge it or name it"
    assert "event" in tenant and "spec" in tenant and "auth_session" in purging.GLOBAL


class _Relay:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, msg) -> str:
        self.sent.append(msg)
        return "id"
