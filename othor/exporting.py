# SPDX-License-Identifier: Apache-2.0
"""Export everything (v0.18, M18.1): one zip of what Othor AI computed and what the owner wrote.

Read-only and deterministic: the same stored data gives the same bytes (entries in a fixed order,
every entry dated to the last sealed run's date, canonical JSON, CSV with fixed columns, the brief
PDFs dated by `brief_pdf.fonts_dated`). Never a secret, token or hash, and never a value read from
a source (no rows ever leave a source; a scanned column's min/max stay behind).

`TABLES` decides, for every table in the metadata, whether it leaves in the export and which
columns stay behind, each with a reason; a guard test fails on a table or column it doesn't name.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.config import Settings
from othor.core.models import AppUser, Base, Company, Event, Membership

EXPORTER_VERSION = 1
# a company over either is exported by a job that emails a link (a PDF takes 1–2 s to render)
LARGE_POINTS = 200_000
LARGE_BRIEFS = 10


@dataclass(frozen=True)
class Rule:
    skip: str = ""  # why the table stays behind; empty: it is exported
    drop: dict[str, str] = field(default_factory=dict)  # column -> why it stays behind


HIDDEN = "a secret: never exported"
SOURCE_VALUE = "values read from a source: never exported"

TABLES: dict[str, Rule] = {
    # the owner's company, people and settings
    "company": Rule(drop={"logo_sha": "an internal file name; the logo is the owner's own"}),
    "app_user": Rule(),  # only the company's members, below
    "membership": Rule(),
    "setup_state": Rule(),
    "summary_layout": Rule(),
    "business_message": Rule(),
    # the Ontology and the specs compiled from it
    "ontology_record": Rule(),
    "ontology_version": Rule(),
    "spec": Rule(),
    "setup_draft": Rule(),
    "setup_change": Rule(drop={"quote": "a piece of an uploaded file; the file is the owner's"}),
    "upload": Rule(drop={"storage_path": "where the encrypted file sits on the server"}),
    "upload_ref": Rule(
        drop={
            "text": "a piece of an uploaded file; the file is the owner's",
            "data": "a piece of an uploaded file; the file is the owner's",
        }
    ),
    "proposal": Rule(),
    "question": Rule(),
    "chat_thread": Rule(),
    "chat_message": Rule(),
    # what the nightly runs computed
    "run": Rule(),
    "run_step": Rule(),
    "run_query": Rule(drop={"params": "bind values; the SQL itself is kept"}),
    "series_meta": Rule(),
    "finding": Rule(),
    "detector_result": Rule(),
    "forecast": Rule(),
    "watch": Rule(),
    "snooze": Rule(),
    "mute": Rule(),
    # briefs and their delivery
    "brief": Rule(),
    "brief_version": Rule(),
    "brief_issue": Rule(),
    "recipient": Rule(),
    "suggested_brief": Rule(),
    "delivery": Rule(),
    "postmark_event": Rule(),
    # Othor AI's Big Picture reports (v0.19): the report as shown, numbers filled by code
    "big_picture": Rule(drop={"input_hash": "an internal cache key"}),
    # Look back's reports (an Enterprise Edition table, named here only; the core never imports
    # it): the question, plan, results and words are the owner's; the upload is not exported
    # Discover's groups (Enterprise Edition, named here only): what was offered and decided
    "discover_group": Rule(),
    "look_back_report": Rule(
        drop={
            "upload_id": "an internal file reference; the file itself is not exported",
            "upload_sha": "an internal file reference",
            "results_hash": "an internal replay check",
        }
    ),
    # sources: names, tables and columns, never how to reach them or what they hold
    "source": Rule(
        drop={
            "config": "how to reach the source (host, user, account)",
            "last_test": "a connection test's details",
        }
    ),
    "source_schema": Rule(),
    "source_table": Rule(drop={"watermark_column": "an internal read position"}),
    "source_column": Rule(drop={"stats": SOURCE_VALUE + " (a column's min and max)"}),
    "sheet_tab": Rule(drop={"shape": SOURCE_VALUE + " (sample cells)"}),
    "scan": Rule(),
    "scan_discovery": Rule(drop={"evidence": SOURCE_VALUE}),
    "connector_request": Rule(),
    # the history: every owner action and system event
    "event": Rule(),
    # AI usage: the counts, never what was sent or returned
    "model_call": Rule(
        drop={
            "input": "the prompt sent; counts only",
            "output": "the reply; counts only",
            "input_hash": "an internal cache key",
        }
    ),
    # stays behind
    "source_secret": Rule(skip=HIDDEN + " (source passwords and Google tokens)"),
    "auth_session": Rule(skip=HIDDEN + " (sign-in sessions)"),
    "login_code": Rule(skip=HIDDEN + " (sign-in codes)"),
    "invitation": Rule(skip=HIDDEN + " (invitation codes)"),
    "link_token": Rule(skip=HIDDEN + " (recipients' link tokens)"),
    "job": Rule(skip="the internal work queue"),
    "rate_limit": Rule(skip="internal rate-limit counters"),
    "worker_heartbeat": Rule(skip="the worker's heartbeat, not company data"),
    "platform_event": Rule(skip="the instance's own log, across companies"),
    # External Signals (Enterprise Edition, named here only): public series, no company data
    "outside_point": Rule(skip="public series (rates, prices, rainfall), not company data"),
    "outside_fetch": Rule(skip="the instance's fetch log of public series"),
    "access_request": Rule(skip="website requests for access, not company data"),
    "purge_lock": Rule(skip="the purge's own lock (M18.3), never company data"),
}


@dataclass(frozen=True)
class Export:
    data: bytes
    filename: str
    manifest: dict[str, Any]


# --- writing ---------------------------------------------------------------------------------


def _default(o: Any) -> Any:
    if isinstance(o, Decimal | uuid.UUID):
        return str(o)
    if isinstance(o, dt.datetime | dt.date | dt.time):
        return o.isoformat()
    if isinstance(o, bytes):
        return None
    raise TypeError(f"not exportable: {type(o).__name__}")


def _json(doc: Any) -> bytes:
    return (
        json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False, default=_default) + "\n"
    ).encode()


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, dict | list):
        return json.dumps(v, sort_keys=True, ensure_ascii=False, default=_default)
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    return str(v)


def _csv(header: list[str], rows: list[list[Any]]) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    return out.getvalue().encode()


# --- reading ---------------------------------------------------------------------------------


def _classes() -> dict[str, type]:
    import othor.all_models  # noqa: F401  (every table in the metadata)

    return {m.local_table.name: m.class_ for m in Base.registry.mappers}  # type: ignore[union-attr]


def rows(s: Session, name: str) -> list[dict[str, Any]]:
    """One table's rows for this company (through the ORM, so the tenancy guard filters them),
    the kept columns only, oldest first."""
    rule = TABLES[name]
    cls = _classes()[name]
    mapper = sa.inspect(cls)
    cols = [c for c in mapper.columns if c.name not in rule.drop]
    q = sa.select(cls)
    if name == "app_user":  # global table: only this company's members
        q = q.where(AppUser.id.in_(sa.select(Membership.user_id)))
    order = [mapper.columns[k] for k in ("created_at", "at") if k in mapper.columns]
    q = q.order_by(*order, mapper.primary_key[0])
    out = []
    for obj in s.scalars(q).all():
        out.append({c.name: getattr(obj, mapper.get_property_by_column(c).key) for c in cols})
    return out


def _last_run(s: Session) -> Any:
    from othor.engine import ledger

    return ledger.last_sealed(s)


def _points(settings: Settings, company_id: uuid.UUID, run: Any) -> list[Any]:
    from othor.engine.store import RunFiles, read_points, sort_points

    if run is None:
        return []
    path = RunFiles(settings.runs_dir(company_id), run.id).series
    return sort_points(read_points(path)) if path.exists() else []


def is_large(s: Session, settings: Settings, company_id: uuid.UUID) -> bool:
    """Too big to build while the owner waits: then a job builds it and emails a link."""
    from othor.briefs.models import Brief
    from othor.engine.store import RunFiles

    briefs = s.scalar(sa.select(sa.func.count(Brief.id)).where(Brief.deleted_at.is_(None))) or 0
    if briefs > LARGE_BRIEFS:
        return True
    run = _last_run(s)
    if run is None:
        return False
    path = RunFiles(settings.runs_dir(company_id), run.id).series
    if not path.exists():
        return False
    import duckdb

    with duckdb.connect() as con:
        got = con.execute("select count(*) from read_parquet(?)", [path.as_posix()]).fetchone()
    return bool(got and got[0] > LARGE_POINTS)


def _pdfs(s: Session, settings: Settings, company: Company, today: dt.date) -> dict[str, bytes]:
    from othor.briefs import service
    from othor.web import brief_pdf
    from othor.web import brief_view as bvw

    out: dict[str, bytes] = {}
    for b in service.active(s):
        bv = bvw.load(s, settings, b.id, None, today)
        if bv is None or bv.issue is None:
            continue
        name = brief_pdf.file_name(company, b.name, bv.issue.date)
        out[f"briefs/{name}"] = brief_pdf.build(s, settings, company, bv)
    return out


README = """Othor AI export · {company}
As of run #{number} ({run_date}) · spec {spec}

What Othor AI computed for you and what you wrote, as it was after that run. Every number
here was computed by code, not written by AI; each finding carries its provenance (the run,
the spec version, the test and its parameters).

  README.txt              this file
  manifest.json           every file with its size and SHA-256
  ontology/spec.json      the spec the last run used (compiled from your Ontology)
  series/last_run.csv     every number the last run computed, by period (aggregates only)
  findings.csv            Sweep's findings, one per line (the full record in data/finding.json)
  events.csv              the history: every change you made and everything the system did
  briefs/*.pdf            each brief's latest issue
  data/<table>.json       everything else, one file per kind of record

Never in this export: passwords, tokens or sign-in codes; how to reach your sources; any
row or value from your sources (they never left them); the text sent to or returned by
the AI model (data/model_call.json has the counts).
"""


def build(s: Session, settings: Settings, company: Company) -> Export:
    """The zip, under the caller's tenant scope. Reads stored results only."""
    run = _last_run(s)
    day = run.run_date if run is not None else dt.date(2026, 1, 1)
    files: dict[str, bytes] = {}
    present = _classes()
    for name, rule in sorted(TABLES.items()):
        # an Enterprise Edition table is named here but absent from an open-core install
        if not rule.skip and name in present:
            files[f"data/{name}.json"] = _json(rows(s, name))
    spec_hash = ""
    if run is not None:
        from othor.ontology.models import Spec

        spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).first()
        if spec is not None:
            files["ontology/spec.json"] = _json(spec.compiled)
            spec_hash = spec.hash
    from othor.engine.models import Finding
    from othor.engine.store import OUTPUT_FIELDS

    fcols = ["run_id", "fingerprint", "detector", "metric", "breakdown", "breakdown_value"]
    fcols += ["state", "confidence", "money", "status", "change", "first_seen_run", "days_open"]
    fs = s.scalars(sa.select(Finding).order_by(Finding.created_at, Finding.id)).all()
    files["findings.csv"] = _csv(fcols, [[getattr(f, c) for c in fcols] for f in fs])
    ecols = ["at", "actor", "action", "object_type", "object_id", "before", "after"]
    es = s.scalars(sa.select(Event).order_by(Event.at, Event.id)).all()
    files["events.csv"] = _csv(ecols, [[getattr(e, c) for c in ecols] for e in es])
    pts = _points(settings, company.id, run)
    files["series/last_run.csv"] = _csv(
        list(OUTPUT_FIELDS), [[getattr(p, f) for f in OUTPUT_FIELDS] for p in pts]
    )
    files.update(_pdfs(s, settings, company, day))
    files["README.txt"] = README.format(
        company=company.name,
        number=f"{run.number:04d}" if run is not None else "—",
        run_date=day.isoformat(),
        spec=spec_hash[:12] or "—",
    ).encode()
    manifest = {
        "company": company.slug,
        "exporter_version": EXPORTER_VERSION,
        "as_of": {
            "run_id": str(run.id) if run is not None else None,
            "run_number": run.number if run is not None else None,
            "run_date": day.isoformat(),
            "spec_hash": spec_hash,
        },
        "files": [
            {"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
            for p, b in sorted(files.items())
        ],
    }
    files["manifest.json"] = _json(manifest)
    return Export(_zip(files, day), f"othor-{company.slug}-export-{day.isoformat()}.zip", manifest)


def _zip(files: dict[str, bytes], day: dt.date) -> bytes:
    buf = io.BytesIO()
    stamp = (day.year, day.month, day.day, 0, 0, 0)
    with zipfile.ZipFile(buf, "w") as z:
        for path in [
            "README.txt",
            "manifest.json",
            *sorted(p for p in files if p not in ("README.txt", "manifest.json")),
        ]:
            info = zipfile.ZipInfo(path, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            z.writestr(info, files[path], compresslevel=6)
    return buf.getvalue()


def write(path: Path, data: bytes) -> None:
    """Atomically: a half-written zip is never served."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".zip.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


# --- the job's file and the Settings row ---------------------------------------------------

KEEP = dt.timedelta(hours=48)  # a built export is kept this long, then deleted


def zip_path(settings: Settings, company_id: uuid.UUID, job_id: uuid.UUID) -> Path:
    return settings.company_dir(company_id) / "exports" / f"{job_id}.zip"


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def state(
    s: Session, settings: Settings, company_id: uuid.UUID, now: dt.datetime
) -> dict[str, Any]:
    """Settings' Your data row: large or not, and the latest export job's state (preparing, or
    ready to download until a time)."""
    from othor.jobs.models import Job

    out: dict[str, Any] = {"large": is_large(s, settings, company_id), "status": ""}
    job = s.scalars(
        sa.select(Job).where(Job.kind == "export").order_by(Job.created_at.desc()).limit(1)
    ).first()
    if job is None:
        return out
    if job.status in ("queued", "running"):
        out["status"] = "preparing"
    elif job.status == "done" and job.finished_at is not None:
        until = _aware(job.finished_at) + KEEP
        if until > now and zip_path(settings, company_id, job.id).exists():
            out.update(status="ready", job_id=str(job.id), until=until)
    return out


def ready(
    s: Session, settings: Settings, company_id: uuid.UUID, job_id: uuid.UUID, now: dt.datetime
) -> Path | None:
    """The built zip, when it is this company's, done and less than 48 hours old."""
    from othor.jobs.models import Job

    job = s.scalars(sa.select(Job).where(Job.id == job_id, Job.kind == "export")).first()
    if job is None or job.status != "done" or job.finished_at is None:
        return None
    if _aware(job.finished_at) + KEEP <= now:
        return None
    path = zip_path(settings, company_id, job.id)
    return path if path.exists() else None


def prune(settings: Settings, now: dt.datetime) -> int:
    """Delete built exports older than 48 hours, on every company's volume (the worker's loop)."""
    root = settings.data_dir / "companies"
    gone = 0
    for path in root.glob("*/exports/*.zip*"):
        made = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.UTC)
        if made + KEEP <= now:
            path.unlink(missing_ok=True)
            gone += 1
    return gone
