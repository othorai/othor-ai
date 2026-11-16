# SPDX-License-Identifier: Apache-2.0
"""Indigo Thread confirmed and connected to the compose sample sources (read-only users), ready for
runs. Needs `make dev && make seed`."""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from othor.connectors import credentials
from othor.connectors.models import Source
from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.tenancy import tenant_scope
from othor.engine.context import EngineContext
from othor.ontology import service
from tests.fixtures.sample_company.catalog import catalog_from_generator
from tests.fixtures.sample_company.ontology import sample_ontology
from tests.integration.setup_world import CONFIG, OWNER

NOW = dt.datetime(2026, 11, 1, 20, 35, tzinfo=dt.UTC)  # 2:05 AM IST on Mon 2 Nov


def require_sources() -> None:
    from othor.connectors.sql.scan import check_connection
    from tests.conftest import require_services
    from tests.integration.test_sql_connectors import my

    if check_connection(my("othor_ro")).message.startswith("Can't reach"):
        require_services("sample sources (make dev && make seed)")


def engine_ctx(sf, tmp_path: Path, clock=lambda: NOW) -> EngineContext:
    settings = Settings(env="test", data_dir=tmp_path, db_url="sqlite://")
    key = InstanceKey.load_or_create(tmp_path / "instance.key", allow_create=True)
    return EngineContext(settings, sf, key, clock=clock)


def confirm_sample(
    ctx: EngineContext,
    cid: uuid.UUID,
    sample,
    *,
    order_tracker: bool = False,
    with_mis: bool = False,
    watches: bool = False,
    e2e: bool = False,
    without: tuple[str, ...] = (),
    surat_answered: bool = False,
) -> dict[str, uuid.UUID]:
    """With `order_tracker`, the Order tracker's tabs are picked and confirmed as on 10c and read
    through the Sheets stand-in (written under the data dir's sample/sheets). With `e2e`, the
    sources are the e2e databases (tests/fixtures/sample_company/e2e.py). `without`: record keys
    left out (applied later, as an owner's answer would be)."""
    from tests.fixtures.sample_company import e2e as e2e_db

    with ctx.sf() as s, tenant_scope(cid):
        titles = (("Order tracker FY27",) if order_tracker else ()) + (
            ("MIS-Weekly",) if with_mis else ()
        )
        ids = catalog_from_generator(
            s, sample, sheets=titles, erp_schema=e2e_db.ERP_DB if e2e else "erp_db"
        )
        for name, sid in ids.items():
            src = s.scalars(sa.select(Source).where(Source.id == sid)).one()
            if src.kind == "gsheets":
                _confirm_tabs(s, src, sample)
                continue
            src.config = e2e_db.config(name) if e2e else CONFIG[name]
            credentials.store(s, ctx.key, cid, sid, {"password": "othor_ro_dev"})
        changes = sample_ontology(order_tracker=order_tracker, with_mis=with_mis)
        changes = [c for c in changes if c.key not in without]
        service.apply(
            s,
            e2e_db.retarget(changes) if e2e else changes,
            actor=OWNER,
            reason="Sample Ontology",
        )
        service.confirm(s, actor=OWNER)
        if surat_answered:  # the 28 Oct Home answer, a new Ontology version (M7)
            from tests.fixtures.sample_company.ontology import surat_answer

            changes = surat_answer()
            service.apply(
                s,
                e2e_db.retarget(changes) if e2e else changes,
                actor=OWNER,
                reason="Answered on Home",
            )
        if watches:
            add_watches(s)
        s.commit()
    return ids


# The four watches of board 5e (P7, P5, P6, P2), with their dates. Indicators are the engine's.
WATCHES = (
    (
        "Dispatch time spread, Coimbatore · 7 days",
        {
            "kind": "value",
            "metric": "dispatch_time_spread",
            "grain": "day",
            "breakdown": "plant",
            "breakdown_value": "Coimbatore",
            "window_days": 7,
        },
        "1.6",
        "Aditya Naik",
        "2026-11-01",
    ),
    (
        "Active customers silent 90+ days",
        {"kind": "finding_slot", "detector": "missing_records", "slot": "count"},
        "8",
        "Priya Menon",
        "2026-11-01",
    ),
    (
        "Freight per metre vs 6-month average",
        {"kind": "vs_average", "metric": "freight_per_metre", "grain": "month", "periods": 6},
        "0.10",
        "Leela Tandon",
        "2026-10-30",
    ),
    (
        "Receivables over 60 days, share",
        {"kind": "value", "metric": "receivables_share_over_60", "grain": "day"},
        "0.12",
        "Leela Tandon",
        "2026-08-16",
    ),
)


def add_watches(s, on: dt.date | None = None, before: dt.date | None = None) -> None:
    """The 5e watches; with `on`, only those started that day (the e2e nights); with `before`,
    only those started before it (the nights' setup, so none is added twice)."""
    from othor.engine import watches

    for label, ind, thr, owner, since in WATCHES:
        day = dt.date.fromisoformat(since)
        if on is not None and day != on:
            continue
        if before is not None and day >= before:
            continue
        watches.create(
            s,
            label=label,
            indicator=ind,
            threshold=thr,
            direction="above",
            owner_name=owner,
            since=dt.date.fromisoformat(since),
            actor=OWNER,
        )


def _confirm_tabs(s, src: Source, sample) -> None:
    from othor.connectors.models import SheetTab, SourceTable
    from othor.connectors.sheets.detect import detect
    from othor.connectors.sheets.extract import header_fingerprint
    from tests.fixtures.sample_company.catalog import TODAY

    doc = next(d for d in sample.sheets.values() if d["properties"]["title"] == src.name)
    tables = {
        t.name: t for t in s.scalars(sa.select(SourceTable).where(SourceTable.source_id == src.id))
    }
    for sheet in doc["sheets"]:
        name = sheet["properties"]["title"]
        t = tables[name]
        if t.use not in ("metric_source", "dimension"):
            continue
        shape = detect(sheet, today=TODAY)
        s.add(
            SheetTab(
                source_id=src.id,
                table_id=t.id,
                spreadsheet_id=doc["spreadsheetId"],
                spreadsheet_title=src.name,
                tab_name=name,
                selected=True,
                status="confirmed",
                header_row=shape.header_row,
                last_row=shape.last_row,
                header_fingerprint=header_fingerprint(shape),
                confirmed_by=OWNER,
            )
        )
    s.flush()


@pytest.fixture
def sqlite_only(db_url):
    if not db_url.startswith("sqlite"):
        pytest.skip("runs read the sample sources; one metadata DB is enough here")
