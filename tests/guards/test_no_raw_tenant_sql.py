# SPDX-License-Identifier: Apache-2.0
"""Raw SQL bypasses the tenancy guard, so product code never runs text() against tenant tables,
and never uses Session.get (the identity map can return rows without a scoped query)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "othor"
ALLOWED = {ROOT / "core" / "tenancy.py"}
# Statements sent to customer sources (catalog reads and aggregates), never to tenant tables;
# they go through the query log's personal-column guard instead.
SOURCE_SQL = ROOT / "connectors" / "sql"
SKIP_DIRS = {ROOT / "migrations"}
TENANT_TABLES = (
    "membership",
    "event",
    "setup_state",
    "source",
    "source_secret",
    "scan",
    "source_schema",
    "source_table",
    "source_column",
    "sheet_tab",
    "scan_discovery",
    "run_query",
    "job",
    "ontology_record",
    "model_call",
    "run",
    "run_step",
    "series_meta",
    "spec",
    "question",
)  # extend as tenant tables are added
# the SQL string passed to text(…) only: matching to the end of the file made a later comment
# ("Run again now") look like SQL on the run table (12 Nov 2026)
SQL = re.compile(r"\btext\(\s*[rf]?(\"\"\"|\'\'\'|\"|\')(.*?)\1", re.S)
TABLE = re.compile(r"\b(" + "|".join(TENANT_TABLES) + r")\b", re.I)


def _raw(src: str) -> bool:
    return any(TABLE.search(m.group(2)) for m in SQL.finditer(src))


SESSION_GET = re.compile(r"\b(session|s|db)\.get\(")


def _py_files() -> list[Path]:
    return [
        p
        for p in ROOT.rglob("*.py")
        if p not in ALLOWED
        and SOURCE_SQL not in p.parents
        and not any(d in p.parents for d in SKIP_DIRS)
    ]


def test_no_raw_sql_on_tenant_tables() -> None:
    hits = [str(p) for p in _py_files() if _raw(p.read_text())]
    assert hits == [], f"raw SQL against tenant tables: {hits}"


def test_no_session_get() -> None:
    hits = [str(p) for p in _py_files() if SESSION_GET.search(p.read_text())]
    assert hits == [], f"use Repo.get, not Session.get: {hits}"


def test_every_model_with_company_id_uses_tenant_mixin() -> None:
    from othor.core.models import AuthSession, Base, Company, LoginCode, PurgeLock, TenantMixin

    # Read before a company scope exists; only othor.core.auth may touch them (checked below).
    # PurgeLock (M18.3) is the purge's own lock, read by the append-only triggers, never data.
    pre_scope = {Company, LoginCode, AuthSession, PurgeLock}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if cls in pre_scope:
            continue
        if "company_id" in mapper.columns:
            assert issubclass(cls, TenantMixin), cls


def test_only_auth_touches_auth_tables() -> None:
    hits = [
        str(p)
        for p in _py_files()
        if p.name != "auth.py"
        and p.name != "models.py"
        and re.search(r"\b(LoginCode|AuthSession|Invitation|AccessRequest)\b", p.read_text())
    ]
    assert hits == [], f"use othor.core.auth, not the auth tables directly: {hits}"


def test_the_guard_reads_only_the_sql_string() -> None:
    assert _raw('s.execute(text("SELECT * FROM job WHERE id = 1"))')
    assert _raw('text("""\n  UPDATE run SET x = 1\n""")')
    assert _raw("text('SELECT count(*) FROM question')")
    assert not _raw('c.execute(sa.text("SELECT 1"))\n# Run again now (v0.11)\n')
