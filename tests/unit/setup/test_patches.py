# SPDX-License-Identifier: Apache-2.0
"""Typed patches (M4b): a chat's proposal is a patch over a metric body, never SQL. Each patch
gives a valid, compilable body (and any records it needs) with the change written line by line;
an invalid patch is refused with a reason."""

from __future__ import annotations

import pytest

from othor.core.tenancy import tenant_scope
from othor.ontology.compiler.build import compile_records
from othor.ontology.compiler.catalog import load_catalog
from othor.ontology.types import validate_body
from othor.setup.patches import Patch, PatchRefused, apply_patch
from tests.fixtures.sample_company.catalog import catalog_from_generator
from tests.fixtures.sample_company.ontology import sample_ontology

COMPANY = {
    "fiscal_year_start_month": 4,
    "timezone": "Asia/Kolkata",
    "currency": "INR",
    "number_format": "indian",
    "week_starts": "monday",
}


@pytest.fixture
def world(session_factory, two_companies, sample):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, sample)
        cat = load_catalog(s)
    records = {(c.type, c.key): validate_body(c.type, c.body) for c in sample_ontology()}
    return cat, records


def _compiles(cat, records, key, body, extra=()):
    recs = {k: v for k, v in records.items() if k[0] != "metric"}
    recs[("metric", key)] = body
    for type_, k, b in extra:
        recs[(type_, k)] = b
    compile_records(recs, cat, COMPANY, 0)


@pytest.mark.parametrize(
    ("key", "patch", "lines", "check"),
    [
        (
            "receivables_over_60",
            Patch("set_threshold", {"value": 90}),
            ["Threshold 60 → 90 days"],
            lambda body: body["name"] == "Receivables over 90 days",
        ),
        (
            "receivables_over_60",
            Patch("exclude", {"breakdown": "segment", "values": ["Government"]}),
            ["Exclude segment = Government"],
            None,
        ),
        (
            "dispatch_time",
            Patch("coalesce_date", {"primary": "ready_at", "fallback": "dispatch_date"}),
            ["Start = ready_at, else dispatch_date"],
            lambda body: "coalesce" in str(body["measure"]["expr"]),
        ),
    ],
    ids=["set_threshold_and_rename", "exclude_breakdown_values", "coalesce_date"],
)
def test_a_patch_writes_its_line_and_still_compiles(world, key, patch, lines, check):
    cat, records = world
    out = apply_patch(records[("metric", key)], patch, cat, records)
    assert out.lines == lines
    assert check is None or check(out.body)
    _compiles(cat, records, key, out.body)


def test_use_column_and_convert_units(world):
    cat, records = world
    body = records[("metric", "metres_dispatched")]
    out = apply_patch(
        body,
        Patch(
            "use_column",
            {
                "table": {"source": "sales_dw", "schema": "sales_dw", "table": "fact_dispatch"},
                "column": "dispatch_mtrs",
                "date": "dispatch_date",
            },
        ),
        cat,
        records,
    )
    assert out.body["measure"]["expr"] == {"col": "dispatch_mtrs"}
    conv = apply_patch(
        out.body,
        Patch(
            "convert_units",
            {
                "column": "dispatch_mtrs",
                "when": {"column": "uom", "value": "YD"},
                "factor": "0.9144",
                "to_unit": "metres",
            },
        ),
        cat,
        records,
    )
    assert conv.lines == ["Convert dispatch_mtrs where uom = YD × 0.9144 (to metres)"]
    ((type_, _key, rule),) = conv.records
    assert type_ == "rule" and rule["kind"] == "unit_conversion"
    _compiles(cat, records, "metres_dispatched", conv.body, conv.records)


@pytest.mark.parametrize(
    "patch, reason",
    [
        (
            Patch(
                "use_column",
                {
                    "table": {"source": "sales_dw", "schema": "sales_dw", "table": "fact_dispatch"},
                    "column": "no_such",
                },
            ),
            "no column",
        ),
        (
            Patch(
                "use_column",
                {
                    "table": {"source": "sales_dw", "schema": "public", "table": "employee"},
                    "column": "phone",
                },
            ),
            "personal",
        ),
        (Patch("add_filter", {"column": "plant", "op": "LIKE", "value": "X"}), "operator"),
        (Patch("set_threshold", {"value": 90}), "no threshold"),
        (Patch("teleport", {}), "unknown"),
    ],
)
def test_invalid_patches_are_refused(world, patch, reason):
    cat, records = world
    body = records[("metric", "metres_dispatched")]
    with pytest.raises(PatchRefused, match=reason):
        apply_patch(body, patch, cat, records)


def test_use_duration_for_a_time_metric(world):
    cat, records = world
    body = {"name": "Dispatch time", "status": "needs_source", "needs": "Not tracked yet"}
    out = apply_patch(
        body,
        Patch(
            "use_duration",
            {
                "table": {"source": "sales_dw", "schema": "sales_dw", "table": "fact_dispatch"},
                "start": "ready_at",
                "end": "dispatched_at",
            },
        ),
        cat,
        records,
    )
    assert out.lines == ["Days from ready_at to dispatched_at, average, by dispatched_at"]
    assert out.body["unit"] == "days" and out.body["measure"]["agg"] == "avg"
    _compiles(cat, records, "dispatch_time", out.body)


def test_threshold_on_a_days_column(world):
    cat, records = world
    body = {
        **records[("metric", "receivables_over_60")],
        "filters": [{"op": ">", "args": [{"col": "days_overdue"}, {"lit": "60"}]}],
    }
    out = apply_patch(body, Patch("set_threshold", {"value": 90}), cat, records)
    assert out.lines == ["Threshold 60 → 90 days"]
    assert out.body["filters"][0]["args"][1] == {"lit": 90}
