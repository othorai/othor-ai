# SPDX-License-Identifier: Apache-2.0
"""The compiled SQL, run read-only on the sample sources, equals the fixture oracles
(tests/fixtures/sample_company/measures.py). Needs `make dev && make seed`."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pandas as pd
import pytest

from othor.connectors.sql.engine import SqlCreds, make_engine
from othor.connectors.sql.query_log import QueryLog, identifiers
from othor.core.tenancy import tenant_scope
from othor.ontology.compiler import compile_version
from othor.ontology.compiler.catalog import load_catalog
from tests.fixtures.sample_company import measures
from tests.integration.test_sql_connectors import my, pg
from tests.unit.ontology.conftest import World, build

OCT = (dt.date(2026, 10, 1), dt.date(2026, 11, 1))
PAISA = Decimal("0.01")


@pytest.fixture
def compiled(session_factory, two_companies, db_url, sample_sources_seeded):
    if not db_url.startswith("sqlite"):
        pytest.skip("the metadata DB doesn't matter here")
    from othor.connectors.sql.scan import check_connection
    from tests.conftest import require_services
    from tests.fixtures.sample_company.generate import generate

    if check_connection(my("othor_ro")).message.startswith("Can't reach"):
        require_services("sample sources (make dev && make seed)")
    sample = generate()
    cid, _ = two_companies
    build(session_factory, cid, sample)
    w = World(session_factory, cid)
    with w.sf() as s, tenant_scope(cid):
        spec = compile_version(s, w.company(s)).compiled
        personal = {
            t.name.lower(): t.personal for t in load_catalog(s).tables.values() if t.personal
        }
        s.commit()
    return spec, sample, personal


def run(creds: SqlCreds, sql: str, log: QueryLog, **params) -> list[tuple]:
    eng = make_engine(creds)
    try:
        with eng.connect() as c:
            return [tuple(r) for r in log.run(c, sql, params)]
    finally:
        eng.dispose()


def test_net_revenue_october_by_region(compiled):
    spec, sample, personal = compiled
    log = QueryLog(personal=personal)
    q = {
        k: spec["metrics"][k]["queries"]["month:region"]
        for k in ("invoiced_value", "credit_note_value", "rebates")
    }
    total: dict[str, Decimal] = {}
    for key, sign in (("invoiced_value", 1), ("credit_note_value", -1), ("rebates", -1)):
        for period, region, value in run(
            my("othor_ro"), q[key], log, **{"from": OCT[0], "to": OCT[1]}
        ):
            assert period == OCT[0]
            total[region] = total.get(region, Decimal(0)) + sign * Decimal(str(value))
    oracle = measures.net_revenue_by_region_month(sample)["2026-10"]
    for region, v in oracle.items():
        assert abs(total.get(region, Decimal(0)) - Decimal(str(v))) <= PAISA, region
    assert abs(sum(total.values()) - Decimal("46200000")) / Decimal("46200000") < Decimal(
        "0.01"
    )  # P1
    for e in log.entries:  # read-only role, nothing personal
        ids = identifiers(e.sql)
        assert not any(t in ids and cols & ids for t, cols in personal.items()), e.sql


def test_metres_dispatched_converts_yards(compiled):
    spec, sample, personal = compiled
    log = QueryLog(personal=personal)
    sql = spec["metrics"]["metres_dispatched"]["queries"]["month:plant"]
    rows = run(pg("othor_ro"), sql, log, **{"from": OCT[0], "to": OCT[1]})
    got = {plant: Decimal(str(v)) for _p, plant, v in rows}
    d = sample.dw["sales_dw"]["fact_dispatch"]
    d = d[[x.strftime("%Y-%m") == "2026-10" for x in d["dispatch_date"]]]
    for plant in ("Surat", "Coimbatore"):
        expect = measures._metres(d[d["plant"] == plant]).sum()
        assert abs(got[plant] - Decimal(str(round(expect, 4)))) <= Decimal("0.01"), plant


def test_receivables_over_60_share_steps_up(compiled):
    """P2: the share over 60 days steps from about 9% to 14% on 14 Aug."""
    spec, sample, personal = compiled
    log = QueryLog(personal=personal)
    over = spec["metrics"]["receivables_over_60"]["queries"]["as_of:"]
    open_ = spec["metrics"]["receivables_open"]["queries"]["as_of:"]
    for day in (dt.date(2026, 7, 31), dt.date(2026, 10, 31)):
        o = Decimal(str(run(my("othor_ro"), over, log, as_of=day)[0][0]))
        a = Decimal(str(run(my("othor_ro"), open_, log, as_of=day)[0][0]))
        oracle = measures.share_over_60(sample, [pd.Timestamp(day)]).iloc[0]
        assert abs(o / a - Decimal(str(oracle))) < Decimal("0.0001"), day
