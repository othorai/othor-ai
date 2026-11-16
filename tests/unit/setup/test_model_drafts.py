# SPDX-License-Identifier: Apache-2.0
"""Model drafts (fake Claude): checked against the scan, typed and validated; never a personal
column; a number not in the inputs is replaced by a sentence built by code."""

from __future__ import annotations

from othor.core.tenancy import tenant_scope
from othor.ontology.compiler.catalog import load_catalog
from othor.setup import drafts
from tests.fakes_model import DRAFT
from tests.fixtures.sample_company.catalog import catalog_from_generator


def _cat(session_factory, two_companies, sample):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, sample)
        return load_catalog(s)


def test_model_output_becomes_typed_drafts(session_factory, two_companies, sample):
    cat = _cat(session_factory, two_companies, sample)
    inputs = drafts.model_inputs(cat, [], "We make textiles.", keys=[], rules=[], confirmed=[])
    got = {d.key: d for d in drafts.from_model(DRAFT, cat, inputs)}

    nr = got["net_revenue"]
    assert nr.confidence == "high" and nr.state == "needs_confirmation"
    assert nr.body["measure"] == {"agg": "sum", "expr": {"col": "net_amount"}}
    assert nr.origin["by"] == "model" and nr.origin["prompt"] == "draft_metrics@v4"
    assert nr.refs[0]["locator"] == "tab Sales!D1"
    assert nr.body["area"] == "Sales"  # v4 (M16.1): the area, filled in quietly

    # an invented number in the wording is replaced by code's sentence
    md = got["metres_dispatched"]
    assert "4,00,000" not in md.body["description"]
    assert (
        md.body["description"] == "Sum of dispatch_mtrs in sales_dw.fact_dispatch by dispatch_date"
    )

    assert got["cash_ratio"].body["formula"]["op"] == "/"

    # personal column → the excluded-table line, never a metric on it
    hc = got["headcount"]
    assert hc.state == "excluded_table" and hc.body["status"] == "needs_source"
    assert hc.note == "public.employee has personal data. It can be used without personal columns."

    gh = got["ghost"]
    assert gh.state == "needs_source" and gh.confidence == "check"

    # an unknown column passes the types; the compiler rejects it when values are computed
    assert got["bad_column"].confidence == "check"
    assert got["bad_column"].note == "Is this the right column?"

    # the model's own reason is kept: it says more than "No source has it" (owner, 12 Nov)
    cs = got["customer_satisfaction_score"]
    assert cs.state == "needs_source" and cs.note == "No survey data"
    assert cs.origin["reason"] is True and cs.body["needs"] == "No survey data"


def test_a_reason_with_an_invented_number_falls_back(session_factory, two_companies, sample):
    cat = _cat(session_factory, two_companies, sample)
    inputs = drafts.model_inputs(cat, [], "We make textiles.", keys=[], rules=[], confirmed=[])
    out = {
        **DRAFT,
        "missing": [{"name": "Win rate", "why": "Only 37 of 912 deals have an outcome"}],
    }
    (wr,) = [d for d in drafts.from_model(out, cat, inputs) if d.key == "win_rate"]
    assert wr.note == drafts.NO_SOURCE and wr.origin["reason"] is False


def test_inputs_carry_no_personal_columns(session_factory, two_companies, sample):
    cat = _cat(session_factory, two_companies, sample)
    inputs = drafts.model_inputs(cat, [], "", keys=[], rules=[], confirmed=[])
    for t in cat.tables.values():
        for name in t.personal:
            for x in inputs["tables"]:
                if x["table"] == t.name:
                    assert name not in {c["column"].lower() for c in x["columns"]}


def test_breakdowns_filters_and_rules(session_factory, two_companies, sample):
    """Breakdowns only from the Ontology (by key or by the column they read); one filter checked
    against the scan; confirmed table rules applied as for rule drafts."""
    cat = _cat(session_factory, two_companies, sample)
    inputs = drafts.model_inputs(cat, [], "", keys=[], rules=[], confirmed=[])
    cancelled = {"op": "<>", "args": [{"col": "status"}, {"lit": "X"}]}
    rules = [
        (
            "cancelled_invoices",
            {
                "kind": "filter",
                "expr": cancelled,
                "table": {"source": "erp_db", "schema": "erp_db", "table": "invoices"},
            },
        )
    ]
    got = {
        d.key: d
        for d in drafts.from_model(
            DRAFT, cat, inputs, breakdowns={"sales_region": "region"}, rules=rules
        )
    }
    nr = got["net_revenue"].body
    assert nr["breakdowns"] == ["sales_region"]  # "region" mapped by column; "currency" dropped
    assert nr["filters"] == [{"op": "=", "args": [{"col": "currency"}, {"lit": "INR"}]}, cancelled]
    assert got["net_revenue"].origin["rule"].endswith("· applies cancelled_invoices")
    # without known breakdowns, none are kept
    plain = {d.key: d for d in drafts.from_model(DRAFT, cat, inputs)}
    assert plain["net_revenue"].body["breakdowns"] == []


def _v3(**m):
    base = {
        "key": "x",
        "name": "X",
        "description": "",
        "breakdowns": [],
        "refs": [],
        "confidence": "high",
        "question": "",
        "filter_column": "",
        "filter_op": "",
        "filter_value": "",
        "formula_op": "",
        "formula_a": "",
        "formula_b": "",
        "times_days_in_period": False,
        "days_from": "",
        "days_to": "",
        "age_column": "",
        "age_over_days": 0,
        "grain": "day",
        "unit": "inr",
        "kind": "flow",
    }
    return {"metrics": [{**base, **m}], "missing": []}


def test_v3_time_between_two_dates_and_older_than_n_days(session_factory, two_companies, sample):
    """Prompt v3 (12 Nov 2026, indigo2): the model can say "dispatched_at minus ready_at" and
    "due_date older than 60 days", which v2 couldn't, so these were wrongly "missing"; each
    compiles like the sample Ontology's own definitions."""
    from othor.ontology.compiler.build import compile_records
    from othor.ontology.types import validate_body
    from tests.fixtures.sample_company.ontology import sample_ontology
    from tests.unit.setup.test_patches import COMPANY

    cat = _cat(session_factory, two_companies, sample)
    inputs = drafts.model_inputs(cat, [], "We make textiles.", keys=[], rules=[], confirmed=[])
    dispatch = _v3(
        key="dispatch_time",
        name="Dispatch time",
        source="sales_dw",
        schema="sales_dw",
        table="fact_dispatch",
        agg="avg",
        date_column="dispatch_date",
        days_from="ready_at",
        days_to="dispatched_at",
    )
    (d,) = drafts.from_model(dispatch, cat, inputs)
    assert d.state == "needs_confirmation" and d.body["unit"] == "days"
    assert d.body["measure"] == {
        "agg": "avg",
        "expr": {"fn": "days_between", "args": [{"col": "dispatched_at"}, {"col": "ready_at"}]},
    }
    aged = _v3(
        key="receivables_over_60",
        name="Receivables over 60 days",
        source="erp_db",
        schema="erp_db",
        table="receivables",
        column="amount",
        agg="sum",
        kind="snapshot",
        date_column="due_date",
        age_column="due_date",
        age_over_days=60,
    )
    (r,) = drafts.from_model(aged, cat, inputs)
    assert r.body["filters"][-1] == {
        "op": ">",
        "args": [
            {"fn": "days_between", "args": [{"param": "as_of"}, {"col": "due_date"}]},
            {"lit": 60},
        ],
    }
    records = {(c.type, c.key): validate_body(c.type, c.body) for c in sample_ontology()}
    recs = {k: v for k, v in records.items() if k[0] != "metric"}
    for x in (d, r):
        compile_records({**recs, ("metric", x.key): x.body}, cat, COMPANY, 0)
    # not a date column, the same column twice, or an age on a flow: left out, never guessed
    bad = _v3(**{**dispatch["metrics"][0], "days_from": "dispatch_mtrs"})
    assert "fn" not in str(drafts.from_model(bad, cat, inputs)[0].body.get("measure"))
    flow = _v3(**{**aged["metrics"][0], "kind": "flow"})
    assert "days_between" not in str(drafts.from_model(flow, cat, inputs)[0].body.get("filters"))


def test_v4_area_from_the_short_list_only(session_factory, two_companies, sample):
    """Prompt v4 (M16.1): the area comes from the short list; Other, or anything else, is none."""
    from tests.fakes_model import metric

    cat = _cat(session_factory, two_companies, sample)
    inputs = drafts.model_inputs(cat, [], "We make textiles.", keys=[], rules=[], confirmed=[])
    base = dict(
        source="erp_db",
        schema="erp_db",
        table="invoices",
        column="net_amount",
        date_column="invoice_date",
    )
    out = {
        "metrics": [
            metric("a", area="Collections", **base),
            metric("b", area="Other", **base),
            metric("c", area="Quality team", **base),
        ],
        "missing": [],
    }
    got = {d.key: d.body.get("area") for d in drafts.from_model(out, cat, inputs)}
    assert got == {"a": "Collections", "b": None, "c": None}
