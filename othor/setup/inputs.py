# SPDX-License-Identifier: Apache-2.0
"""What setup drafts from: the scanned catalog, upload refs, the Ontology records so far (rules,
vocabulary), the business message. Read inside the company's tenant scope."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.connectors import credentials
from othor.connectors.models import Source
from othor.connectors.sql.engine import SqlCreds
from othor.core.crypto import InstanceKey
from othor.ontology import service
from othor.ontology.compiler.catalog import Catalog, load_catalog
from othor.setup.models import BusinessMessage, Upload, UploadRef
from othor.setup.parse import Ref

Records = dict[tuple[str, str], dict[str, Any]]


@dataclass
class SetupInputs:
    cat: Catalog
    refs: list[tuple[str, Ref]]
    uploads: dict[str, uuid.UUID]  # filename → upload id
    records: Records  # the Ontology records so far (draft before Confirm)
    message: str
    mentioned: set[str] = field(default_factory=set)

    @property
    def rules(self) -> list[tuple[str, dict[str, Any]]]:
        return [(k, b) for (t, k), b in sorted(self.records.items()) if t == "rule"]

    @property
    def vocabulary(self) -> list[dict[str, Any]]:
        return [b for (t, _), b in sorted(self.records.items()) if t in ("vocabulary", "alias")]

    @property
    def breakdowns(self) -> dict[str, dict[str, Any]]:
        return {k: b for (t, k), b in sorted(self.records.items()) if t == "breakdown"}

    @property
    def personal(self) -> dict[str, set[str]]:
        return {t.name.lower(): t.personal for t in self.cat.tables.values() if t.personal}

    @property
    def base(self) -> Records:
        """Records the drafts compile against: everything but metrics."""
        return {k: b for k, b in self.records.items() if k[0] != "metric"}


def gather(s: Session) -> SetupInputs:
    cat = load_catalog(s)
    ups = {u.id: u for u in s.scalars(sa.select(Upload).where(Upload.deleted_at.is_(None)))}
    refs = []
    for ur in s.scalars(sa.select(UploadRef).order_by(UploadRef.upload_id, UploadRef.locator)):
        u = ups.get(ur.upload_id)
        if u is not None:
            refs.append((u.filename, Ref(ur.kind, ur.locator, ur.text, ur.data or {})))
    records = {(r.type, r.key): r.body for r in service.current(s)}
    msg = s.scalars(sa.select(BusinessMessage).order_by(BusinessMessage.version.desc())).first()
    text = msg.text if msg else ""
    mentioned = {r.text.split(".")[0].lower() for _, r in refs if r.kind == "table"}
    mentioned |= {name for name in cat.sources if name.lower() in text.lower()}
    for _, r in refs:
        for name in cat.sources:
            if name.lower() in r.text.lower():
                mentioned.add(name)
    return SetupInputs(
        cat, refs, {u.filename: u.id for u in ups.values()}, records, text, mentioned
    )


def creds_loader(s: Session, key: InstanceKey, company_id: uuid.UUID) -> dict[str, SqlCreds]:
    from othor.jobs.scan_job import sql_creds

    out = {}
    for src in s.scalars(
        sa.select(Source).where(Source.deleted_at.is_(None), Source.kind.in_(("postgres", "mysql")))
    ):
        secret = credentials.load(s, key, company_id, src.id)
        if secret:
            out[src.name] = sql_creds(src, secret)
    return out
