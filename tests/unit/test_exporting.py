# SPDX-License-Identifier: Apache-2.0
"""Export everything (M18.1): a deterministic zip of what Othor AI computed and the owner wrote,
never a secret, token or source value. Built from the 1 Nov night of the sample company."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile

import sqlalchemy as sa

from othor import exporting
from othor.connectors import credentials
from othor.connectors.models import Source, SourceColumn, SourceSecret
from othor.core.models import AuthSession, Company, LoginCode
from othor.core.tenancy import system_scope, tenant_scope
from othor.engine import ledger
from othor.engine.store import OUTPUT_FIELDS, RunFiles, read_points, sort_points
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _export(svc, cid):
    with svc.sf() as s, tenant_scope(cid):
        company = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        return exporting.build(s, svc.settings, company)


def _entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {i.filename: z.read(i) for i in z.infolist()}


def test_two_builds_are_byte_identical_and_the_manifest_checks(night):  # noqa: F811
    _env, _h, cid, _rid, svc = night
    a, b = _export(svc, cid), _export(svc, cid)
    assert a.data == b.data
    files = _entries(a.data)
    man = json.loads(files["manifest.json"])
    listed = {f["path"]: f for f in man["files"]}
    assert set(listed) == set(files) - {"manifest.json"}
    for path, body in files.items():
        if path != "manifest.json":
            assert listed[path]["sha256"] == hashlib.sha256(body).hexdigest()
            assert listed[path]["bytes"] == len(body)
    assert a.filename == f"othor-indigothread-export-{man['as_of']['run_date']}.zip"
    with zipfile.ZipFile(io.BytesIO(a.data)) as z:  # the dates are the run's, not the clock's
        assert {i.date_time for i in z.infolist()} == {
            (*tuple(int(x) for x in man["as_of"]["run_date"].split("-")), 0, 0, 0)
        }


def test_what_the_zip_holds(night):  # noqa: F811
    _env, _h, cid, _rid, svc = night
    files = _entries(_export(svc, cid).data)
    for path in (
        "README.txt",
        "ontology/spec.json",
        "data/ontology_record.json",
        "data/ontology_version.json",
        "data/brief.json",
        "data/brief_issue.json",
        "data/finding.json",
        "data/forecast.json",
        "data/event.json",
        "data/company.json",
        "findings.csv",
        "events.csv",
        "series/last_run.csv",
    ):
        assert path in files, path
    assert any(p.startswith("briefs/") and p.endswith(".pdf") for p in files)
    assert (
        files["briefs/" + next(p for p in files if p.endswith(".pdf")).split("/", 1)[1]][:5]
        == b"%PDF-"
    )
    for p, body in files.items():
        if p.endswith(".json"):
            json.loads(body)
    readme = files["README.txt"].decode()
    assert "Indigo Thread" in readme and "computed by code" in readme
    rows = list(csv.DictReader(io.StringIO(files["findings.csv"].decode())))
    assert rows and {"fingerprint", "detector", "metric", "money", "run_id"} <= set(rows[0])
    # 0.35: events.csv carries the plain who and what, as the Audit log shows them
    evs = list(csv.DictReader(io.StringIO(files["events.csv"].decode())))
    assert evs and {"actor", "action", "who", "what"} <= set(evs[0])
    done = [e for e in evs if e["action"] == "run.finished"]
    assert done and done[0]["who"] == "System"
    assert done[0]["what"].startswith("Nightly run #") and done[0]["what"].endswith(" finished")


def test_no_secret_or_token_is_in_the_zip(night):  # noqa: F811
    _env, _h, cid, _rid, svc = night
    blob = b"\n".join(_entries(_export(svc, cid).data).values())
    needles: list[str] = []
    with svc.sf() as s, tenant_scope(cid):
        for src in s.scalars(sa.select(Source)).all():
            secret = credentials.load(s, svc.key, cid, src.id) or {}
            needles += [str(v) for v in secret.values() if len(str(v)) >= 6]
            for k in ("host", "user", "account"):  # how to reach the source is not exported
                v = (src.config or {}).get(k)
                if v and len(str(v)) >= 6:
                    needles.append(str(v))
        assert s.scalars(sa.select(SourceSecret)).first() is not None
        needles += [r.blob.hex()[:24] for r in s.scalars(sa.select(SourceSecret)).all()]
    with svc.sf() as s, system_scope("test: the session and code hashes"):
        needles += [r.token_hash for r in s.scalars(sa.select(AuthSession)).all()]
        needles += [r.code_hash for r in s.scalars(sa.select(LoginCode)).all()]
    assert needles
    for n in needles:
        assert n.encode() not in blob, n[:4] + "…"


def test_no_source_value_is_in_the_zip(night):  # noqa: F811
    """A column's min and max (scan stats) are values read from the source: never exported."""
    _env, _h, cid, _rid, svc = night
    files = _entries(_export(svc, cid).data)
    cols = json.loads(files["data/source_column.json"])
    assert cols and all("stats" not in c for c in cols)
    blob = b"\n".join(files.values())
    with svc.sf() as s, tenant_scope(cid):
        stats = [c.stats for c in s.scalars(sa.select(SourceColumn)).all() if c.stats]
    vals = {str(v) for st in stats for k, v in st.items() if k in ("min", "max") and v}
    for v in (v for v in vals if len(v) >= 8):
        assert v.encode() not in blob, v


def test_the_series_are_the_last_runs(night):  # noqa: F811
    _env, _h, cid, _rid, svc = night
    files = _entries(_export(svc, cid).data)
    with svc.sf() as s, tenant_scope(cid):
        run = ledger.last_sealed(s)
        assert run is not None
        pts = sort_points(read_points(RunFiles(svc.settings.runs_dir(cid), run.id).series))
    rows = list(csv.reader(io.StringIO(files["series/last_run.csv"].decode())))
    assert rows[0] == list(OUTPUT_FIELDS)
    assert len(rows) - 1 == len(pts)
    assert rows[1][0] == pts[0].metric and rows[1][5] == str(pts[0].value)


def test_every_column_is_exported_or_excluded_with_a_reason():
    """A new table or column fails here until someone decides whether it leaves in the export
    (the purge in M18.3 reads the same list)."""
    import othor.all_models as m

    # a test plug-in's own tables (tests/plugins) are loaded by the plug-in tests in the same
    # process; they are never part of an install
    test_only = {
        mp.local_table.name
        for mp in m.Base.registry.mappers
        if mp.class_.__module__.startswith("tests.")
    }
    for t in m.Base.metadata.tables.values():
        if t.name in test_only:
            continue
        rule = exporting.TABLES.get(t.name)
        assert rule is not None, f"{t.name}: add it to exporting.TABLES"
        if rule.skip:
            assert rule.skip.strip(), t.name
            continue
        for col, why in rule.drop.items():
            assert col in t.columns, f"{t.name}.{col} no longer exists"
            assert why.strip(), f"{t.name}.{col}"
    assert exporting.TABLES["source_secret"].skip
    assert "stats" in exporting.TABLES["source_column"].drop
    assert "config" in exporting.TABLES["source"].drop
    assert {"input", "output"} <= set(exporting.TABLES["model_call"].drop)
