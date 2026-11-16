# SPDX-License-Identifier: Apache-2.0
"""A company with the sample catalog (from the generator) and the sample Ontology applied."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session, sessionmaker

from othor.core.models import Company
from othor.core.tenancy import Repo, tenant_scope
from othor.ontology import service
from tests.fixtures.sample_company.catalog import catalog_from_generator
from tests.fixtures.sample_company.generate import SampleCompany, generate
from tests.fixtures.sample_company.ontology import sample_ontology

OWNER = "owner-1"


@pytest.fixture(scope="session")
def sample() -> SampleCompany:
    return generate()


@dataclass
class World:
    sf: sessionmaker[Session]
    cid: uuid.UUID

    def session(self) -> Callable[[], object]:
        return self.sf

    def company(self, s: Session) -> Company:
        c = Repo(s).get(Company, self.cid)
        assert c is not None
        return c


def build(
    sf: sessionmaker[Session], cid: uuid.UUID, sample: SampleCompany, *, order_tracker: bool = False
) -> None:
    with sf() as s, tenant_scope(cid):
        catalog_from_generator(s, sample, sheets=order_tracker)
        draft = service.apply(
            s, sample_ontology(order_tracker=order_tracker), actor=OWNER, reason="Sample Ontology"
        )
        assert draft is None  # before Confirm: drafts, no version
        v = service.confirm(s, actor=OWNER)
        assert v.number == 1
        s.commit()


@pytest.fixture
def world(session_factory, two_companies, sample) -> World:
    cid, _ = two_companies
    build(session_factory, cid, sample)
    return World(session_factory, cid)
