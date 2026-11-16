# SPDX-License-Identifier: Apache-2.0
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core.events import SYSTEM_ACTOR, record_event
from othor.core.models import Event
from othor.core.tenancy import AppendOnlyViolation, Repo, tenant_scope

Ids = tuple[uuid.UUID, uuid.UUID]


def test_record_event_writes_actor_object_before_after(
    session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, _ = two_companies
    with session_factory() as s, tenant_scope(a):
        ev = record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="company.renamed",
            object_type="company",
            object_id=a,
            before={"name": "Indigo Thread"},
            after={"name": "Indigo Thread Co.", "n": Decimal("1.50")},
        )
        s.commit()
        got = Repo(s).get(Event, ev.id)
        assert got is not None
        assert got.company_id == a and got.workspace_id == a
        assert got.actor == "system" and got.action == "company.renamed"
        assert got.object_type == "company" and got.object_id == str(a)
        assert got.before == {"name": "Indigo Thread"}
        assert got.after == {"name": "Indigo Thread Co.", "n": "1.50"}
        assert got.at.tzinfo is not None


def _one_event(sf: sessionmaker[Session], company: uuid.UUID) -> uuid.UUID:
    with sf() as s, tenant_scope(company):
        ev = record_event(s, actor="system", action="x", object_type="t", object_id="1")
        s.commit()
        return ev.id


def test_orm_update_and_delete_refused(
    session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, _ = two_companies
    eid = _one_event(session_factory, a)
    with session_factory() as s, tenant_scope(a):
        ev = Repo(s).get(Event, eid)
        assert ev is not None
        ev.action = "changed"
        with pytest.raises(AppendOnlyViolation):
            s.flush()
    with session_factory() as s, tenant_scope(a):
        ev = Repo(s).get(Event, eid)
        s.delete(ev)
        with pytest.raises(AppendOnlyViolation):
            s.flush()
    with session_factory() as s, tenant_scope(a):
        with pytest.raises(AppendOnlyViolation):
            s.execute(sa.update(Event).values(action="bulk"))
        with pytest.raises(AppendOnlyViolation):
            s.execute(sa.delete(Event))


def test_database_trigger_refuses_raw_sql(
    session_factory: sessionmaker[Session], two_companies: Ids
) -> None:
    a, _ = two_companies
    _one_event(session_factory, a)
    engine = session_factory.kw["bind"]
    for stmt in ("UPDATE event SET action = 'raw'", "DELETE FROM event"):
        with pytest.raises(sa.exc.DatabaseError, match="append-only"), engine.begin() as c:
            c.execute(sa.text(stmt))
    with engine.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM event")).scalar_one() == 1
