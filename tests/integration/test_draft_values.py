# SPDX-License-Identifier: Apache-2.0
"""Accept (docs/10 M4): every value in the Metrics table matches a direct SQL check.

Drafts (the sample Ontology's metrics as drafts, and the rule drafts) are computed by compiling
them and running their SQL read-only on the sample sources; each value is compared with an
independent oracle (measures.py) or a hand-written query."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pandas as pd
import pytest
import sqlalchemy as sa

from othor.connectors.sql.engine import make_engine
from othor.connectors.sql.query_log import QueryLog, identifiers
from othor.core.tenancy import tenant_scope
from othor.ontology.compiler.catalog import load_catalog
from othor.setup import parse
from othor.setup.compute import Runner, compile_drafts, last_period
from othor.setup.rules_draft import rule_drafts
from tests.fixtures.sample_company import measures
from tests.fixtures.sample_company.catalog import catalog_from_generator
from tests.fixtures.sample_company.generate import generate
from tests.fixtures.sample_company.ontology import sample_ontology
from tests.fixtures.sample_company.uploads import write_uploads
from tests.integration.test_sql_connectors import my, pg

TODAY = dt.date(2026, 11, 4)
COMPANY = {
    "fiscal_year_start_month": 4,
    "timezone": "Asia/Kolkata",
    "currency": "INR",
    "number_format": "indian",
    "week_starts": "monday",
}
PAISA = Decimal("0.01")


@pytest.fixture(scope="module")
def sample():
    return generate()


@pytest.fixture
def world(session_factory, two_companies, db_url, sample, sample_sources_seeded):
    if not db_url.startswith("sqlite"):
        pytest.skip("the metadata DB doesn't matter here")
    from othor.connectors.sql.scan import check_connection
    from tests.conftest import require_services

    if check_connection(my("othor_ro")).message.startswith("Can't reach"):
        require_services("sample sources (make dev && make seed)")
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, sample)
        cat = load_catalog(s)
        personal = {t.name.lower(): t.personal for t in cat.tables.values() if t.personal}
        s.commit()
    return cat, personal


def _runner(spec, cat, personal):
    log = QueryLog(personal=personal)
    creds = {"erp_db": my("othor_ro"), "sales_dw": pg("othor_ro")}
    return Runner(spec, cat, creds.get, log), log


def _sample_drafts():
    out = {}
    for c in sample_ontology():
        out[(c.type, c.key)] = c.body
    return out


def test_sample_metrics_as_drafts_equal_the_oracles(world, sample):
    cat, personal = world
    from othor.ontology.types import validate_body

    drafts = {k: validate_body(k[0], b) for k, b in _sample_drafts().items()}
    spec, errors = compile_drafts({}, drafts, cat, COMPANY)
    assert errors == {}
    run, log = _runner(spec, cat, personal)
    try:
        nr = run.value("net_revenue", TODAY)
        assert (nr.start, nr.end) == (dt.date(2026, 10, 1), dt.date(2026, 11, 1))
        oracle = Decimal(str(measures.monthly_net_revenue(sample)["2026-10"]))
        assert abs(nr.value - oracle) <= PAISA
        # metres yesterday (3 Nov), yards converted at Coimbatore
        md = run.value("metres_dispatched", TODAY)
        d = sample.dw["sales_dw"]["fact_dispatch"]
        y = d[[x == dt.date(2026, 11, 3) for x in d["dispatch_date"]]]
        assert abs(md.value - Decimal(str(round(measures._metres(y).sum(), 6)))) <= PAISA
        # receivables over 60 days as of 3 Nov
        ro = run.value("receivables_over_60", TODAY)
        day = pd.Timestamp("2026-11-03")
        o = measures._open_receivables(sample, day)
        age = (day - pd.to_datetime(o["due_date"])).dt.days
        assert abs(ro.value - Decimal(str(round(o.loc[age > 60, "open_amount"].sum(), 2)))) <= PAISA
        # DSO for October: open receivables at 31 Oct ÷ October net revenue × 31
        dso = run.value("dso", TODAY)
        assert abs(
            dso.value - Decimal(str(measures.dso_days(sample, dt.date(2026, 10, 31))))
        ) < Decimal("0.0001")
    finally:
        run.close()
    for e in log.entries:  # nothing personal was read
        ids = identifiers(e.sql)
        assert not any(t in ids and cols & ids for t, cols in personal.items()), e.sql


def test_rule_drafts_equal_hand_written_sql(world, sample, tmp_path):
    cat, personal = world
    refs = []
    for p in write_uploads(sample, tmp_path):
        got = parse.parse_file(p.read_bytes(), parse.kind_of(p.name), p.name)
        refs += [(p.name, r) for r in got.refs]
    drafts = {("metric", d.key): d.body for d in rule_drafts(cat, refs, rules=[], vocabulary=[])}
    spec, errors = compile_drafts({}, drafts, cat, COMPANY)
    assert errors == {}
    run, _ = _runner(spec, cat, personal)
    erp = make_engine(my("othor_ro"))
    dw = make_engine(pg("othor_ro"))
    try:
        for (_, key), body in sorted(drafts.items()):
            got = run.value(key, TODAY)
            t = body["table"]
            eng = erp if t["source"] == "erp_db" else dw
            q = (
                f'"{t["schema"]}"."{t["table"]}"'
                if eng is dw
                else f"`{t['schema']}`.`{t['table']}`"
            )
            start, end = last_period(body["grain"], TODAY)
            if body["kind"] == "snapshot":
                continue  # receivables over 60 is checked against the oracle above
            m = body["measure"]
            agg = "COUNT(*)" if m["agg"] == "count" else f"SUM({m['expr']['col']})"
            sql = f"SELECT {agg} FROM {q} WHERE {body['date']} >= :a AND {body['date']} < :b"
            with eng.connect() as c:
                want = c.execute(sa.text(sql), {"a": start, "b": end}).scalar() or 0
            assert abs(got.value - Decimal(str(want))) <= PAISA, (key, got.value, want)
    finally:
        run.close()
        erp.dispose()
        dw.dispose()
