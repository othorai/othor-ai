# SPDX-License-Identifier: Apache-2.0
"""Compiler: Ontology → spec (docs/04 §1, docs/10 M3).

Accept: compiling the sample Ontology gives the same spec hash twice (and in a fresh process);
a metric touching a personal column is rejected.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import sqlalchemy as sa
import sqlglot

from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.ontology import service
from othor.ontology.compiler import PersonalColumnRejected, compile_records, compile_version
from othor.ontology.compiler.api import canonical, company_settings, spec_hash
from othor.ontology.compiler.build import render_base
from othor.ontology.compiler.catalog import load_catalog
from othor.ontology.compiler.render import CompileError
from othor.ontology.models import Question, Spec
from othor.ontology.questions import unmapped_plant_questions
from tests.fixtures.sample_company.ontology import BOARD_NUMBERS, T, col, lit, op
from tests.unit.ontology.conftest import OWNER, build


def _compile(world):
    with world.sf() as s, tenant_scope(world.cid):
        spec = compile_version(s, world.company(s))
        s.commit()
        return spec.hash, spec.compiled


def _records(world):
    with world.sf() as s, tenant_scope(world.cid):
        return (
            {(r.type, r.key): r.body for r in service.active(s)},
            load_catalog(s),
            company_settings(world.company(s)),
        )


# --- accept 1: the same hash ------------------------------------------------------------------


def test_same_hash_twice_and_stored_once(world):
    h1, doc = _compile(world)
    h2, _ = _compile(world)
    assert h1 == h2 and len(h1) == 64
    records, cat, company = _records(world)
    assert spec_hash(compile_records(records, cat, company, 1)) == h1  # recompiled, not cached
    with world.sf() as s, tenant_scope(world.cid):
        assert s.scalar(sa.select(sa.func.count()).select_from(Spec)) == 1
        assert s.scalars(sa.select(Event).where(Event.action == "spec.compiled")).first()
    listed = sorted(k for k, m in doc["metrics"].items() if m.get("listed", True))
    assert listed == sorted(BOARD_NUMBERS)  # the 16 numbers on board 6a1


def test_same_hash_in_a_fresh_process(world, db_url):
    if not db_url.startswith("sqlite"):
        pytest.skip("one database is enough")
    h, _ = _compile(world)
    records, cat, company = _records(world)
    payload = Path(db_url.removeprefix("sqlite:///")).with_suffix(".records.json")
    payload.write_text(
        json.dumps(
            {
                "records": [[t, k, b] for (t, k), b in records.items()],
                "catalog": {
                    "/".join(key): {
                        **info.__dict__,
                        "columns": {n: c.__dict__ for n, c in info.columns.items()},
                    }
                    for key, info in cat.tables.items()
                },
                "sources": cat.sources,
                "company": company,
            }
        )
    )
    code = (
        "import json,sys\n"
        "from othor.ontology.compiler.build import compile_records\n"
        "from othor.ontology.compiler.api import spec_hash\n"
        "from othor.ontology.compiler.catalog import Catalog, TableInfo, ColumnInfo\n"
        "d=json.load(open(sys.argv[1]))\n"
        "cat=Catalog(sources={k: tuple(v) for k, v in d['sources'].items()})\n"
        "for key, t in d['catalog'].items():\n"
        "    t['columns']={n: ColumnInfo(**c) for n, c in t['columns'].items()}\n"
        "    cat.tables[tuple(key.split('/'))]=TableInfo(**t)\n"
        "recs={(t,k): b for t,k,b in reversed(d['records'])}\n"  # different insertion order
        "print(spec_hash(compile_records(recs, cat, d['company'], 1)))\n"
    )
    for seed in ("1", "12345"):
        out = subprocess.run(
            [sys.executable, "-c", code, str(payload)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert out.stdout.strip() == h


def test_a_changed_record_changes_the_hash(world):
    h, _ = _compile(world)
    records, cat, company = _records(world)
    changed = copy.deepcopy(records)
    changed[("rule", "cancelled_invoices")]["expr"]["args"][1]["lit"] = "C"
    assert spec_hash(compile_records(changed, cat, company, 1)) != h


def test_spec_rows_are_append_only(world):
    _compile(world)
    with world.sf() as s, tenant_scope(world.cid):
        spec = s.scalars(sa.select(Spec)).one()
        spec.hash = "x" * 64
        with pytest.raises(Exception, match="append-only"):
            s.flush()


def test_canonical_json_refuses_floats():
    with pytest.raises(TypeError, match="floats"):
        canonical({"x": 0.1})


# --- accept 2: personal columns are rejected ---------------------------------------------------


def _try(world, *changes):
    with world.sf() as s, tenant_scope(world.cid):
        service.apply(s, list(changes), actor=OWNER, reason="test")
        try:
            compile_version(s, world.company(s))
        finally:
            s.commit()


EMP = T("sales_dw", "public", "employee")


@pytest.mark.parametrize(
    "body",
    [
        {
            "name": "x",
            "table": EMP,
            "measure": {"agg": "count_distinct", "expr": col("name")},
            "date": "join_date",
        },
        {
            "name": "x",
            "table": EMP,
            "measure": {"agg": "count"},
            "date": "join_date",
            "filters": [op("=", col("pan"), lit("ABCDE1234F"))],
        },
        {
            "name": "x",
            "table": T("erp_db", "erp_db", "invoices"),
            "measure": {"agg": "sum", "expr": col("net_amount")},
            "date": "invoice_date",
            "breakdowns": ["contact"],
        },
        {
            "name": "x",
            "table": T("sales_dw", "sales_dw", "invoice_contacts"),
            "measure": {"agg": "count"},
            "kind": "snapshot",
            "evidence": ["phone"],
        },
    ],
    ids=["sum-employee-name", "filter-pan", "breakdown-contact-email", "evidence-phone"],
)
def test_personal_column_is_rejected(world, body):
    extra = [
        service.put(
            "breakdown",
            "contact",
            {"name": "Contact", "column": "contact_email", "table": "customers"},
        )
    ]
    with pytest.raises(PersonalColumnRejected) as e:
        _try(world, *extra, service.put("metric", "bad_metric", body))
    assert e.value.metric == "bad_metric"
    assert "ABCDE" not in str(e.value)  # never a value
    with world.sf() as s, tenant_scope(world.cid):
        ev = s.scalars(sa.select(Event).where(Event.action == "spec.rejected")).one()
        assert ev.after["metric"] == "bad_metric"


def test_join_on_a_personal_column_is_rejected(world):
    with pytest.raises(PersonalColumnRejected, match="crm_contacts"):
        _try(
            world,
            service.put(
                "rule",
                "join_bad",
                {
                    "kind": "join",
                    "table": T("sales_dw", "sales_dw", "fact_order"),
                    "to": T("sales_dw", "sales_dw", "crm_contacts"),
                    "on": [("customer_key", "email")],
                },
            ),
            service.put(
                "breakdown", "role", {"name": "Role", "column": "role", "table": "crm_contacts"}
            ),
            service.put(
                "metric",
                "bad_metric",
                {
                    "name": "x",
                    "table": T("sales_dw", "sales_dw", "fact_order"),
                    "measure": {"agg": "count"},
                    "date": "order_date",
                    "breakdowns": ["role"],
                },
            ),
        )


def test_excluded_table_needs_without_personal(world):
    body = {
        "name": "Headcount",
        "table": EMP,
        "measure": {"agg": "count"},
        "kind": "snapshot",
        "breakdowns": ["plant"],
    }
    with pytest.raises(PersonalColumnRejected, match="Excluded"):
        _try(world, service.put("metric", "headcount", body))
    # used without personal columns: allowed, with an explicit column list
    with world.sf() as s, tenant_scope(world.cid):
        from othor.connectors.models import SourceTable

        emp = s.scalars(sa.select(SourceTable).where(SourceTable.name == "employee")).one()
        emp.without_personal = True
        s.commit()
    with world.sf() as s, tenant_scope(world.cid):
        spec = compile_version(s, world.company(s))
        sql = spec.compiled["metrics"]["headcount"]["queries"]["as_of:plant"]
        s.commit()
    names = {c.name for c in sqlglot.parse_one(sql, read="postgres").find_all(sqlglot.exp.Column)}
    assert names == {"plant"} and "*" not in sql.replace("COUNT(*)", "")


def test_recheck_catches_a_corrupted_render(world, monkeypatch):
    from othor.connectors.models import SourceTable
    from othor.ontology.compiler import build as b

    with world.sf() as s, tenant_scope(world.cid):
        s.scalars(
            sa.select(SourceTable).where(SourceTable.name == "employee")
        ).one().without_personal = True
        s.commit()
    real = b.to_sql
    monkeypatch.setattr(b, "to_sql", lambda e, d: real(e, d).replace('"plant"', '"name"', 1))
    with pytest.raises(PersonalColumnRejected, match="rendered SQL"):
        _try(
            world,
            service.put(
                "metric",
                "headcount",
                {
                    "name": "Headcount",
                    "table": EMP,
                    "measure": {"agg": "count"},
                    "kind": "snapshot",
                    "breakdowns": ["plant"],
                },
            ),
        )


def test_unknown_column_and_ignored_table(world):
    with pytest.raises(CompileError, match="isn't in the scan"):
        _try(
            world,
            service.put(
                "metric",
                "bad_metric",
                {
                    "name": "x",
                    "table": T("erp_db", "erp_db", "invoices"),
                    "measure": {"agg": "sum", "expr": col("no_such")},
                    "date": "invoice_date",
                },
            ),
        )
    with pytest.raises(CompileError, match="ignored"):
        _try(
            world,
            service.retire("metric", "bad_metric"),
            service.put(
                "metric",
                "bad2",
                {
                    "name": "x",
                    "table": T("sales_dw", "public", "etl_log"),
                    "measure": {"agg": "count"},
                    "date": "started_at",
                },
            ),
        )


# --- what the spec says ----------------------------------------------------------------------


def test_labels_and_run_locations(world):
    _, doc = _compile(world)
    m = doc["metrics"]
    assert (
        m["gross_margin_per_metre"]["source_label"]
        == "erp_db (MySQL) + sales_dw (PostgreSQL) · derived"
    )
    assert (
        m["gross_margin_per_metre"]["run_location"] == "joined in Othor AI from erp_db and sales_dw"
    )
    assert (
        m["metres_dispatched"]["run_location"]
        == "runs in place on sales_dw (PostgreSQL) · read-only"
    )
    assert m["cash_balance"]["run_location"] == "runs in place on erp_db (MySQL) · read-only"
    assert m["order_book_value"]["status"] == "needs_source"
    assert m["win_rate"]["status"] == "needs_source" and "queries" not in m["win_rate"]


def test_order_tracker_metrics_compile_to_duckdb(session_factory, two_companies, sample):
    cid, _ = two_companies
    build(session_factory, cid, sample, order_tracker=True)
    with session_factory() as s, tenant_scope(cid):
        from othor.core.models import Company
        from othor.core.tenancy import Repo

        doc = compile_version(s, Repo(s).get(Company, cid)).compiled
    ob = doc["metrics"]["order_book_value"]
    assert (
        ob["dialect"] == "duckdb"
        and ob["run_location"] == "read from Google Sheets · Order tracker FY27"
    )
    assert '"sheet_' in ob["queries"]["as_of:stage"]
    assert doc["metrics"]["win_rate"]["type"] == "derived"


def test_backfill_classification(world):
    _, doc = _compile(world)
    bf = {
        k: v.get("backfill")
        for k, v in doc["metrics"].items()
        if v.get("kind") == "snapshot" and v.get("status") == "active"
    }
    assert bf["receivables_over_60"]["possible"] is True
    assert set(bf["receivables_over_60"]["as_of_columns"]) >= {
        "invoice_date",
        "paid_date",
        "due_date",
    }
    assert bf["cash_balance"]["possible"] is True
    assert bf["active_customers"] == {"possible": True, "as_of_columns": ["invoice_date"]}


def test_backfill_not_possible_for_sheets_and_status_only(session_factory, two_companies, sample):
    cid, _ = two_companies
    build(session_factory, cid, sample, order_tracker=True)
    with session_factory() as s, tenant_scope(cid):
        from othor.core.models import Company
        from othor.core.tenancy import Repo

        service.apply(
            s,
            [
                service.put(
                    "metric",
                    "open_order_count",
                    {
                        "name": "Open orders",
                        "table": T("sales_dw", "sales_dw", "fact_order"),
                        "measure": {"agg": "count"},
                        "kind": "snapshot",
                        "filters": [op("<>", col("status"), lit("Invoiced"))],
                    },
                )
            ],
            actor=OWNER,
            reason="t",
        )
        doc = compile_version(s, Repo(s).get(Company, cid)).compiled
    assert doc["metrics"]["order_book_value"]["backfill"]["possible"] is False
    assert doc["metrics"]["open_order_count"]["backfill"]["possible"] is False


def test_spec_carries_suppressions_goals_reads_and_caps(world):
    _, doc = _compile(world)
    assert {s["key"] for s in doc["suppressions"]} == {
        "diwali_week",
        "surat_shutdown",
        "quarter_end_push",
        "skarfab_pause",
    }
    assert {"net_revenue", "gross_margin_per_metre", "dso"} <= set(doc["forecast"]["headline"])
    assert doc["breakdowns"]["region"]["cap"] == 50
    assert doc["read"]["erp_db:erp_db.invoices"]["lookback_days"] == 35
    assert doc["company"]["fiscal_year_start_month"] == 4
    checks = {c["metric"]: c for c in doc["unmapped_checks"]}
    assert set(checks) == {"line_revenue", "returned_metres"}  # both read plant from item codes
    assert checks["line_revenue"] == {
        "metric": "line_revenue",
        "breakdown": "plant",
        "bucket": "Unmapped",
        "threshold": "0.01",
        "source": "erp_db",
        "question": "Which plant does item code {prefix} belong to?",
    }


def test_every_rendered_sql_parses_and_is_a_select(world):
    _, doc = _compile(world)
    n = 0
    for m in doc["metrics"].values():
        d = {"postgres": "postgres", "mysql": "mysql", "duckdb": "duckdb"}.get(
            m.get("dialect", ""), "duckdb"
        )
        for sql in [*m.get("queries", {}).values(), m.get("evidence")]:
            if sql:
                tree = sqlglot.parse_one(sql.replace(":", "__p_") if d != "duckdb" else sql, read=d)
                assert isinstance(tree, sqlglot.exp.Select | sqlglot.exp.Union)
                n += 1
    assert n > 150


def test_cancelled_filter_on_every_invoice_read(world):
    _, doc = _compile(world)
    for key in ("invoiced_value", "export_invoiced", "line_revenue", "active_customers"):
        for sql in doc["metrics"][key]["queries"].values():
            assert "<> 'X'" in sql, key


# --- run the compiled SQL on the generator's data (DuckDB) ---------------------------------


def _duck(sample, extra_item: bool = False, drop_month: str | None = None):
    con = duckdb.connect()
    con.execute("CREATE SCHEMA erp_db")
    for name in ("invoices", "invoice_lines", "cost_ledger", "customers", "credit_notes", "items"):
        df = sample.erp[name].copy()
        if name == "invoice_lines" and extra_item:
            inv = sample.erp["invoices"]
            oct_inv = inv[[d.strftime("%Y-%m") == "2026-10" for d in inv["invoice_date"]]].iloc[0]
            df = pd.concat(
                [
                    df,
                    pd.DataFrame(
                        [
                            {
                                "line_id": 10**7,
                                "invoice_id": oct_inv["invoice_id"],
                                "line_no": 9,
                                "item_code": "XY-0001",
                                "qty_m": 100.0,
                                "rate": 100.0,
                                "net_amount": 2_500_000.0,
                            }
                        ]
                    ),
                ]
            )
        con.register("df", df)
        con.execute(f'CREATE TABLE erp_db."{name}" AS SELECT * FROM df')
        con.unregister("df")
    return con


def _run(con, sql, **params):
    p = {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in params.items()}
    return con.execute(sql, p).fetchall()


def test_unmapped_prefix_lands_in_unmapped_and_raises_one_question(world, sample):
    records, cat, _ = _records(world)
    sql = render_base(records, cat, "line_revenue", "duckdb")
    con = _duck(sample, extra_item=True)
    rows = _run(
        con,
        sql["queries"]["month:plant"],
        **{"from": dt.date(2026, 10, 1), "to": dt.date(2026, 11, 1)},
    )
    by_plant = {r[1]: Decimal(str(r[2])) for r in rows}
    assert set(by_plant) == {"Surat", "Coimbatore", "Unmapped"}
    assert by_plant["Unmapped"] == Decimal("2500000.0")
    um = _run(
        con, sql["unmapped"]["sql"], **{"from": dt.date(2026, 10, 1), "to": dt.date(2026, 11, 1)}
    )
    month = dt.date(2026, 10, 1)
    unmapped = [(month, r[1], Decimal(str(r[2]))) for r in um]
    assert [u[1] for u in unmapped] == ["XY-"]
    totals = {month: sum(by_plant.values())}
    _, doc = _compile(world)
    check = next(c for c in doc["unmapped_checks"] if c["metric"] == "line_revenue")
    with world.sf() as s, tenant_scope(world.cid):
        made = unmapped_plant_questions(s, check, unmapped, totals)
        again = unmapped_plant_questions(s, check, unmapped, totals)
        small = unmapped_plant_questions(s, check, [(month, "ZZ-", Decimal("10"))], totals)
        s.commit()
        assert [q.text for q in made] == ["Which plant does item code XY- belong to?"]
        assert again == [] and small == []
        assert s.scalar(sa.select(sa.func.count()).select_from(Question)) == 1


def test_missing_side_is_not_determinable_never_zero(world):
    _, doc = _compile(world)
    sql = doc["metrics"]["gross_margin_per_metre"]["queries"]["month:plant"]
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE series (metric TEXT, grain TEXT, breakdown TEXT, breakdown_value TEXT, "
        "period DATE, value DECIMAL(20,4))"
    )
    rows = [  # by plant, net revenue (credit notes and rebates allocated, decided 4 Nov)
        ("net_revenue", "month", "plant", "Surat", "2026-10-01", 5000000),
        ("cogs", "month", "plant", "Surat", "2026-10-01", 3000000),
        ("metres_dispatched", "month", "plant", "Surat", "2026-10-01", 50000),
        # Coimbatore: metres but no revenue or cost → those count as zero (same source, flow)
        ("metres_dispatched", "month", "plant", "Coimbatore", "2026-10-01", 20000),
        # Surat in September: revenue and cost but no metres → not determinable
        ("net_revenue", "month", "plant", "Surat", "2026-09-01", 4000000),
        ("cogs", "month", "plant", "Surat", "2026-09-01", 2000000),
    ]
    con.executemany("INSERT INTO series VALUES (?, ?, ?, ?, ?, ?)", rows)
    out = {(str(r[0]), r[1]): (r[2], r[3]) for r in con.execute(sql).fetchall()}
    assert out[("2026-10-01", "Surat")] == (Decimal("40"), "ok")
    assert out[("2026-09-01", "Surat")] == (None, "not_determinable")
    assert out[("2026-10-01", "Coimbatore")][1] == "ok"


# --- M5: the snapshot back-fill, one grouped query against a calendar -------------------------


PAISA = Decimal("0.01")


def _sundays(first: dt.date, last: dt.date) -> list[dt.date]:
    out, d = [], first
    while d <= last:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


@pytest.mark.parametrize(
    ("metric", "bkey"),
    [("receivables_over_60", ""), ("receivables_over_60", "region"), ("cash_balance", "bank")],
)
def test_backfill_query_equals_the_as_of_query_at_every_point(world, sample, metric, bkey):
    records, cat, _ = _records(world)
    sql = render_base(records, cat, metric, "duckdb")
    assert f"backfill:{bkey}" in sql["queries"] and "history_start" in sql["queries"]
    con = _duck(sample)
    for name in ("receivables", "bank_ledger"):
        con.register("df", sample.erp[name])
        con.execute(f'CREATE TABLE erp_db."{name}" AS SELECT * FROM df')
        con.unregister("df")
    days = _sundays(dt.date(2026, 6, 7), dt.date(2026, 10, 25))
    got: dict[tuple[dt.date, str | None], Decimal] = {}
    for r in _run(con, sql["queries"][f"backfill:{bkey}"], **{"from": days[0], "to": days[-1]}):
        got[(r[0], r[1] if bkey else None)] = Decimal(str(r[-1]))
    assert {d for d, _ in got} == set(days)  # one row per Sunday, none outside
    for day in days[::4]:
        for r in _run(con, sql["queries"][f"as_of:{bkey}"], as_of=day):
            k = (day, r[0] if bkey else None)
            # the fixture's pandas columns are floats; real sources are DECIMAL
            assert abs(got[k] - Decimal(str(r[-1]))) < PAISA, k
    start = _run(con, sql["queries"]["history_start"])[0]
    assert min(d for d in start if d is not None) <= dt.date(2024, 4, 30)


def test_backfill_queries_only_for_backfillable_snapshots(world):
    _, doc = _compile(world)
    for key, m in doc["metrics"].items():
        has = any(k.startswith("backfill:") for k in m.get("queries", {}))
        assert has == bool((m.get("backfill") or {}).get("possible")), key
    rec = doc["metrics"]["receivables_over_60"]["queries"]["backfill:region"]
    assert "WITH RECURSIVE" in rec and "GROUP BY" in rec and ":as_of" not in rec


# --- decided 4 Nov: credit notes and rebates allocated to plants -----------------------------


def _plants_oracle(sample, kind):
    """Pandas, from the rendered tables: the same allocation the rule describes."""
    e = sample.erp
    lines = e["invoice_lines"].merge(
        e["invoices"][["invoice_id", "customer_id", "invoice_date"]], on="invoice_id"
    )
    lines["plant"] = (
        lines["item_code"].str[:3].map({"SU-": "Surat", "CB-": "Coimbatore"}).fillna("Unmapped")
    )
    if kind == "credit_notes":
        w = lines.groupby(["invoice_id", "plant"])["net_amount"].sum()
        share = w / w.groupby(level=0).sum()
        cn = e["credit_notes"]
        oct_ = cn[pd.to_datetime(cn["cn_date"]).dt.strftime("%Y-%m") == "2026-10"]
        out = {}
        for _, r in oct_.iterrows():
            parts = share.get(r["invoice_id"])
            if parts is None:
                out["Unmapped"] = out.get("Unmapped", 0) + r["net_amount"]
                continue
            for plant, s in parts.items():
                out[plant] = out.get(plant, 0) + r["net_amount"] * s
        return out
    lines["month"] = pd.to_datetime(lines["invoice_date"]).dt.strftime("%Y-%m")
    w = lines.groupby(["customer_id", "month", "plant"])["net_amount"].sum()
    share = w / w.groupby(level=[0, 1]).sum()
    led = e["cost_ledger"]
    rb = led[
        (led["account_code"] == 4410)
        & (pd.to_datetime(led["entry_date"]).dt.strftime("%Y-%m") == "2026-10")
    ]
    out = {}
    for _, r in rb.iterrows():
        try:
            parts = share.loc[(r["customer_id"], "2026-10")]
        except KeyError:
            out["Unmapped"] = out.get("Unmapped", 0) + r["amount"]
            continue
        for plant, s in parts.items():
            out[plant] = out.get(plant, 0) + r["amount"] * s
    return out


@pytest.mark.parametrize(
    ("metric", "kind"), [("credit_note_value", "credit_notes"), ("rebates", "rebates")]
)
def test_allocation_to_plants_adds_up_and_matches_the_rule(world, sample, metric, kind):
    records, cat, _ = _records(world)
    sql = render_base(records, cat, metric, "duckdb")["queries"]["month:plant"]
    assert "alloc_w" in sql and "Unmapped" in sql
    con = _duck(sample)
    params = {"from": dt.date(2026, 10, 1), "to": dt.date(2026, 11, 1)}
    by_plant = {r[1]: Decimal(str(r[2])) for r in _run(con, sql, **params)}
    total = Decimal(
        str(
            _run(con, render_base(records, cat, metric, "duckdb")["queries"]["month:"], **params)[
                0
            ][1]
        )
    )
    assert abs(sum(by_plant.values()) - total) < Decimal("0.05")  # every rupee goes somewhere
    oracle = _plants_oracle(sample, kind)
    for plant, v in oracle.items():
        assert abs(by_plant.get(plant, Decimal(0)) - Decimal(str(v))) < Decimal("1"), plant


def test_rules_without_text_are_said_from_the_rule(world):
    """The metric panel (4f1) lists every rule that applies, joins and allocations included:
    a rule without its own text gets a sentence generated from the rule (decided 5 Nov 2026)."""
    _, spec = _compile(world)
    rules = {r["key"]: r for r in spec["rules"]}
    assert all(r["text"] for r in rules.values())
    joins = [
        r for r in rules.values() if r["kind"] == "join" and r["text"].startswith("Joined to ")
    ]
    assert joins and any("(MySQL) on " in r["text"] for r in joins)
    assert all(r["applies_to"] is not None for r in rules.values())
