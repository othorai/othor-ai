# SPDX-License-Identifier: Apache-2.0
"""The sample company at the Metrics step: erp_db and sales_dw in the catalog (read-only users of
the compose sample sources), the scan's date statistics, and the five upload fixtures read."""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import sqlalchemy as sa

from othor.ai.gateway import ModelGateway, TemplateProvider
from othor.connectors import credentials
from othor.connectors.models import Source, SourceColumn, SourceTable
from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.models import SetupState
from othor.core.tenancy import tenant_scope
from othor.jobs.queue import enqueue
from othor.jobs.runner import WorkerContext, run_pending
from othor.setup import uploads
from othor.setup.models import BusinessMessage
from tests.fixtures.sample_company.catalog import catalog_from_generator
from tests.fixtures.sample_company.uploads import write_uploads

NOW = dt.datetime(2026, 11, 4, 4, 30, tzinfo=dt.UTC)
OWNER = "00000000-0000-0000-0000-00000000000a"
CONFIG = {
    "erp_db": {
        "host": "localhost",
        "port": 3307,
        "database": "erp_db",
        "user": "othor_ro",
        "ssl": "disable",
    },
    "sales_dw": {
        "host": "localhost",
        "port": 5443,
        "database": "sales_dw",
        "user": "othor_ro",
        "ssl": "disable",
        "schemas": ["public", "sales_dw"],
    },
}
STATS = (
    ("invoices", "invoice_date", "2024-04-01", "2026-11-03"),
    ("fact_invoice", "invoice_date", "2021-04-01", "2026-11-03"),
)
MESSAGE = (
    "We make technical textiles in Surat and Coimbatore. Revenue is in erp_db; the "
    "data warehouse sales_dw has dispatches."
)


def context(
    session_factory,
    tmp_path: Path,
    provider: str = "template",
    api_key: str = "",
    gateway_provider=None,
) -> WorkerContext:
    settings = Settings(
        env="test",
        data_dir=tmp_path,
        db_url="sqlite://",
        model_provider=provider,
        model_api_key=api_key,
    )
    key = InstanceKey.load_or_create(tmp_path / "instance.key", allow_create=True)
    from othor.ai.gateway import provider_for

    gw = ModelGateway(
        session_factory, gateway_provider or provider_for(settings), clock=lambda: NOW
    )
    return WorkerContext(settings, session_factory, key, clock=lambda: NOW, gateway=gw)


def connect_sample(sf, key: InstanceKey, cid: uuid.UUID, sample) -> dict[str, uuid.UUID]:
    """erp_db and sales_dw in the catalog, with the read-only users' credentials and the
    scan's date statistics (as a scan would leave them)."""
    with sf() as s, tenant_scope(cid):
        ids = catalog_from_generator(s, sample)
        for name, sid in ids.items():
            src = s.scalars(sa.select(Source).where(Source.id == sid)).one()
            src.config = CONFIG[name]
            credentials.store(s, key, cid, sid, {"password": "othor_ro_dev"})
        for table, col, lo, hi in STATS:
            c = s.scalars(
                sa.select(SourceColumn)
                .join(SourceTable, SourceTable.id == SourceColumn.table_id)
                .where(SourceTable.name == table, SourceColumn.name == col)
            ).one()
            c.stats = {"min": lo, "max": hi}
        s.commit()
    return ids


def build(
    ctx: WorkerContext, cid: uuid.UUID, sample, tmp_path: Path, skip: tuple[str, ...] = ()
) -> dict[str, uuid.UUID]:
    files = [f for f in write_uploads(sample, tmp_path / "files") if f.name not in skip]
    ids = connect_sample(ctx.sf, ctx.key, cid, sample)
    with ctx.sf() as s, tenant_scope(cid):
        for f in files:
            up, _ = uploads.add(
                s, ctx.settings, ctx.key, cid, filename=f.name, data=f.read_bytes(), actor=OWNER
            )
            enqueue(s, "upload", {"upload_id": str(up.id)})
        s.add(BusinessMessage(text=MESSAGE))
        s.add(SetupState(current_step="metrics"))
        s.commit()
    run_pending(ctx)
    return ids


def draft(ctx: WorkerContext, cid: uuid.UUID, mode: str = "draft") -> None:
    with ctx.sf() as s, tenant_scope(cid):
        enqueue(s, "draft", {"mode": mode, "actor": OWNER})
        s.commit()
    run_pending(ctx)


__all__ = ["NOW", "OWNER", "TemplateProvider", "build", "context", "draft"]
