# SPDX-License-Identifier: Apache-2.0
"""0.29 (Discover 2.0): a breakdown whose value is computed per row, e.g. an age band from the due
date and the run's as-of date. Rendered like a filter, so the personal-column guard applies; a
flow can't read the as-of date (only a snapshot has one)."""

from __future__ import annotations

import copy

import pytest

from othor.ontology.compiler import PersonalColumnRejected, compile_records
from othor.ontology.compiler.render import CompileError
from othor.ontology.types import validate_body
from tests.unit.ontology.test_compiler import _records

AS_OF = {"param": "as_of"}
AGE = {
    "case": [
        [
            {
                "op": "<=",
                "args": [{"fn": "days_between", "args": [AS_OF, {"col": "due_date"}]}, {"lit": 30}],
            },
            {"lit": "0–30 days"},
        ],
        [
            {
                "op": "<=",
                "args": [{"fn": "days_between", "args": [AS_OF, {"col": "due_date"}]}, {"lit": 60}],
            },
            {"lit": "31–60 days"},
        ],
        [
            {
                "op": "<=",
                "args": [{"fn": "days_between", "args": [AS_OF, {"col": "due_date"}]}, {"lit": 90}],
            },
            {"lit": "61–90 days"},
        ],
    ],
    "else": {"lit": "Over 90 days"},
}


def _with(world, bd: dict, metric: str, edit=None):  # type: ignore[no-untyped-def]
    records, cat, company = _records(world)
    records = dict(records)
    records[("breakdown", "age_band")] = validate_body("breakdown", bd)
    m = copy.deepcopy(records[("metric", metric)])
    m["breakdowns"] = [*m.get("breakdowns", []), "age_band"]
    if edit:
        edit(m)
    records[("metric", metric)] = m
    return compile_records(records, cat, company, 0)


def test_an_age_band_on_a_snapshot_compiles_per_band(world) -> None:  # type: ignore[no-untyped-def]
    spec = _with(world, {"name": "Age", "expr": AGE}, "receivables_open")
    q = spec["metrics"]["receivables_open"]["queries"]
    sql = next(v for k, v in q.items() if k.endswith(":age_band"))
    assert "CASE" in sql.upper() and "Over 90 days" in sql
    # the weekly back-fill reads the calendar's date for :as_of, so history has bands too
    back = q.get("backfill:age_band")
    assert back and "Over 90 days" in back and ":as_of" not in back.replace(" ", "")


def test_a_flow_cant_read_the_as_of_date(world) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(CompileError, match="only a snapshot"):
        _with(world, {"name": "Age", "expr": AGE}, "invoiced_value")


def test_a_personal_column_in_the_expression_is_refused(world) -> None:  # type: ignore[no-untyped-def]
    bad = {
        "name": "Who",
        "expr": {
            "case": [
                [
                    {"op": "not_null", "args": [{"col": "contact_email", "table": "customers"}]},
                    {"lit": "x"},
                ]
            ],
            "else": {"lit": "y"},
        },
    }
    with pytest.raises((PersonalColumnRejected, CompileError)):
        _with(world, bad, "receivables_open")


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Both", "column": "region", "expr": AGE},
        {"name": "Neither"},
        {"name": "Vocab", "expr": AGE, "vocabulary": "plant_codes"},
    ],
)
def test_a_breakdown_reads_a_column_or_an_expression(body) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        validate_body("breakdown", body)


def test_the_expression_is_part_of_the_spec(world) -> None:  # type: ignore[no-untyped-def]
    from othor.ontology.compiler.api import canonical

    a = _with(world, {"name": "Age", "expr": AGE}, "receivables_open")
    other = copy.deepcopy(AGE)
    other["else"] = {"lit": "Older"}
    b = _with(world, {"name": "Age", "expr": other}, "receivables_open")
    assert canonical(a) != canonical(b)
