# SPDX-License-Identifier: Apache-2.0
"""`othor admin`: operator reports across companies, read-only, no recipient addresses."""

from __future__ import annotations

import datetime as dt
import json
import types
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from othor.admin import report
from othor.ai.models import ModelCall
from othor.cli import app
from othor.connectors.models import RunQuery, Source
from othor.core.config import Settings
from othor.core.models import AuthSession, Company, Event, SetupState
from othor.core.tenancy import system_scope, tenant_scope
from othor.jobs.models import Job
from othor.mail.models import Delivery
from othor.ontology.models import OntologyRecord
from tests.unit.mail import world

UTC = dt.UTC


def _populate(sf: sessionmaker[Session], a: uuid.UUID, b: uuid.UUID, now: dt.datetime) -> None:
    """Company A: two sources (one connected), two metrics confirmed, a failed and a done run,
    emails sent / failed / bounced, model calls and source queries. Company B: nothing yet."""
    w = world.build(sf, a)
    today = now.date()
    w.night(
        today - dt.timedelta(days=1), fail_step="read:erp_db", status="failed", with_issue=False
    )
    w.night(today)
    with sf() as s, tenant_scope(a):
        s.add(SetupState(current_step="done"))
        src = Source(kind="postgresql", name="sales_dw", status="connected", connected_at=now)
        s.add_all([src, Source(kind="mysql", name="erp_db", status="draft")])
        s.flush()
        for key in ("net_revenue", "gross_margin"):
            s.add(OntologyRecord(type="metric", key=key, created_by="test"))
        s.add(OntologyRecord(type="metric", key="old", status="retired", created_by="test"))
        s.add_all(
            [
                Delivery(kind="brief", ref="brief:1", status="sent", sent_at=now),
                Delivery(kind="brief", ref="brief:2", status="failed", error="relay timeout"),
                Delivery(kind="brief", ref="brief:3", status="bounced"),
                Job(kind="read", status="failed", attempts=3, error="timeout", finished_at=now),
                Job(kind="run", status="queued"),
                ModelCall(
                    prompt_id="brief_prose",
                    prompt_version="v3",
                    provider="fake",
                    model_id="m",
                    input_hash="h",
                    input={},
                    tokens_in=1200,
                    tokens_out=300,
                    guard_result="passed",
                ),
                ModelCall(
                    prompt_id="brief_prose",
                    prompt_version="v3",
                    provider="fake",
                    model_id="m",
                    input_hash="h",
                    input={},
                    tokens_in=0,
                    tokens_out=0,
                    guard_result="passed",
                    cached=True,
                ),
                RunQuery(
                    purpose="run",
                    source_id=src.id,
                    sql="select 1",
                    rows=40,
                    duration_ms=812,
                    hash="q",
                ),
                RunQuery(
                    purpose="run",
                    source_id=src.id,
                    sql="select 2",
                    rows=2,
                    duration_ms=95,
                    hash="q",
                ),
            ]
        )
        s.commit()
    with sf() as s, system_scope("test"):
        from othor.core.models import AppUser

        u = s.scalars(sa.select(AppUser).where(AppUser.email == "uk@indigothread.example")).one()
        s.add(
            AuthSession(
                company_id=a,
                workspace_id=a,
                user_id=u.id,
                expires_at=now + dt.timedelta(days=1),
                last_seen_at=now,
            )
        )
        s.commit()


@pytest.fixture
def populated(session_factory, two_companies):  # type: ignore[no-untyped-def]
    now = dt.datetime.now(UTC)
    a, b = two_companies
    _populate(session_factory, a, b, now)
    return types.SimpleNamespace(sf=session_factory, a=a, b=b, now=now)


def test_companies_lists_every_company_with_its_progress(populated) -> None:  # type: ignore[no-untyped-def]
    sf = populated.sf
    with sf() as s:
        rows = {r["slug"]: r for r in report.companies(s)}
    assert set(rows) == {"indigothread", "otherco"}
    ba = rows["indigothread"]
    assert ba["owner"] == "uk@indigothread.example"
    assert ba["setup_step"] == "done"
    assert (ba["sources_connected"], ba["sources"]) == (1, 2)
    assert ba["metrics_confirmed"] == 2
    assert ba["last_sign_in"] is not None
    other = rows["otherco"]
    assert (other["sources"], other["metrics_confirmed"], other["last_sign_in"]) == (0, 0, None)


def test_runs_shows_last_run_failed_steps_jobs_and_queue(populated) -> None:  # type: ignore[no-untyped-def]
    sf, now = populated.sf, populated.now
    with sf() as s:
        doc = report.runs(s, now=now, days=7)
    ba = {r["slug"]: r for r in doc["companies"]}["indigothread"]
    assert ba["last_status"] == "done" and ba["last_run"] == "#0002"
    assert ba["last_seconds"] == 240
    assert (ba["runs_in_window"], ba["failed_in_window"]) == (2, 1)
    assert [(f["slug"], f["step"]) for f in doc["failed_steps"]] == [
        ("indigothread", "read:erp_db")
    ]
    assert [(j["kind"], j["attempts"]) for j in doc["failed_jobs"]] == [("read", 3)]
    assert doc["queue"]["queued"] == 1 and doc["queue"]["due_now"] == 1


def test_delivery_counts_without_recipient_addresses(populated) -> None:  # type: ignore[no-untyped-def]
    sf, now = populated.sf, populated.now
    with sf() as s:
        doc = report.delivery(s, now=now, days=7)
    ba = {r["slug"]: r for r in doc["companies"]}["indigothread"]
    assert (ba["sent"], ba["failed"], ba["bounced"], ba["issues_ready"]) == (1, 1, 1, 1)
    assert {f["status"] for f in doc["failures"]} == {"failed", "bounced"}
    assert "uk@indigothread.example" not in json.dumps(doc, default=str)


def test_usage_sums_tokens_queries_and_storage(populated, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    sf, a, now = populated.sf, populated.a, populated.now
    settings = Settings(data_dir=tmp_path)
    d = settings.company_dir(a) / "runs"
    d.mkdir(parents=True)
    (d / "points.parquet").write_bytes(b"x" * 2048)
    with sf() as s:
        doc = report.usage(s, now=now, days=30, settings=settings)
    ba = {r["slug"]: r for r in doc["companies"]}["indigothread"]
    assert (ba["model_calls"], ba["tokens_in"], ba["tokens_out"]) == (2, 1200, 300)
    assert (ba["queries"], ba["rows_back"], ba["longest_query_ms"]) == (2, 42, 812)
    assert ba["storage_bytes"] == 2048
    assert {r["slug"]: r["storage_bytes"] for r in doc["companies"]}["otherco"] is None
    assert doc["prompts"] == [
        {
            "slug": "indigothread",
            "prompt": "brief_prose@v3",
            "calls": 2,
            "cached": 1,
            "tokens_in": 1200,
            "tokens_out": 300,
        }
    ]
    assert "select 1" not in json.dumps(doc, default=str)  # no SQL in the report


def test_window_and_company_filter(populated) -> None:  # type: ignore[no-untyped-def]
    sf, now = populated.sf, populated.now
    later = now + dt.timedelta(days=40)
    with sf() as s:
        doc = report.usage(s, now=later, days=7, settings=Settings(data_dir=Path("/nonexistent")))
        assert all(r["model_calls"] == 0 for r in doc["companies"])
        assert [r["slug"] for r in report.companies(s, slug="otherco")] == ["otherco"]


def test_reports_write_nothing(populated) -> None:  # type: ignore[no-untyped-def]
    sf, now = populated.sf, populated.now

    def counts() -> tuple[int, ...]:
        with sf() as s, system_scope("test"):
            return tuple(
                s.scalar(sa.select(sa.func.count()).select_from(t))
                for t in (Event, Company, Job, Delivery, SetupState)
            )

    before = counts()
    with sf() as s:
        report.companies(s)
        report.runs(s, now=now, days=7)
        report.delivery(s, now=now, days=7)
        report.usage(s, now=now, days=30, settings=Settings(data_dir=Path("/nonexistent")))
    assert counts() == before


def test_cli_admin_prints_tables_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from othor.core import config
    from othor.core.db import make_engine, make_session_factory
    from othor.migrations.runner import upgrade

    url = f"sqlite:///{tmp_path / 'cli.db'}"
    upgrade(url)
    monkeypatch.setenv("OTHOR_DB_URL", url)
    monkeypatch.setenv("OTHOR_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    try:
        sf = make_session_factory(make_engine(url))
        with sf() as s, system_scope("test"):
            s.add(Company(slug="indigothread", name="Indigo Thread Co."))
            s.commit()
        for cmd in ("companies", "runs", "delivery", "usage"):
            res = CliRunner().invoke(app, ["admin", cmd])
            assert res.exit_code == 0, res.output
            assert "indigothread" in res.output
            res = CliRunner().invoke(app, ["admin", cmd, "--json", "--company", "indigothread"])
            assert res.exit_code == 0, res.output
            json.loads(res.output)
    finally:
        config.get_settings.cache_clear()
