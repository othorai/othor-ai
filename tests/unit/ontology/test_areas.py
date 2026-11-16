# SPDX-License-Identifier: Apache-2.0
"""Areas in the Ontology (M16.1): an optional `area` on each metric record, compiled into the
spec with the formula's signed terms. An area is a grouping, never a change of meaning: it keeps
the metric's definition hash, so its series and its findings carry on."""

from __future__ import annotations

import pytest

from othor.core.tenancy import tenant_scope
from othor.ontology import areas, service
from othor.ontology.compiler import compile_records, compile_version
from othor.ontology.compiler.terms import terms
from othor.ontology.types import validate_body
from tests.fixtures.sample_company.ontology import m, op
from tests.unit.ontology.test_compiler import _records

BASE = {
    "name": "Orders",
    "table": {"source": "s", "schema": "x", "table": "t"},
    "measure": {"agg": "count"},
    "date": "d",
}


# --- the record --------------------------------------------------------------------------------


def test_area_is_optional_and_old_bodies_stay_byte_identical() -> None:
    assert "area" not in validate_body("metric", BASE)
    assert validate_body("metric", {**BASE, "area": "  Sales "})["area"] == "Sales"
    assert validate_body("metric", {**BASE, "area": "sales"})["area"] == "Sales"
    assert validate_body("metric", {**BASE, "area": "Export desk"})["area"] == "Export desk"


@pytest.mark.parametrize("other", ["Other", "other numbers", "", "   "])
def test_other_means_no_area(other: str) -> None:
    assert "area" not in validate_body("metric", {**BASE, "area": other})


@pytest.mark.parametrize("bad", ["Top 10", "x" * 41, 5])
def test_an_area_is_a_short_name_without_numbers(bad: object) -> None:
    with pytest.raises(ValueError):
        validate_body("metric", {**BASE, "area": bad})


def test_the_select_lists_the_short_list_then_the_owners_names() -> None:
    got = areas.in_use([{"area": "Sales"}, {"area": "Export desk"}, {}, {"area": "Export desk"}])
    assert got == [*areas.SHORT, "Export desk"]
    assert areas.label(None) == "Other numbers"


# --- the signed terms ------------------------------------------------------------------------


def test_terms_of_a_nested_sum() -> None:
    f = op("-", op("-", m("a", "zero"), m("b", "zero")), m("c", "zero"))
    assert terms(f) == [
        {"metric": "a", "sign": 1},
        {"metric": "b", "sign": -1},
        {"metric": "c", "sign": -1},
    ]
    # a minus of a sum flips every part of it
    assert terms(op("-", m("a"), op("+", m("b"), m("c")))) == [
        {"metric": "a", "sign": 1},
        {"metric": "b", "sign": -1},
        {"metric": "c", "sign": -1},
    ]
    assert terms(op("+", m("a"), m("b"), m("c"))) == [
        {"metric": x, "sign": 1} for x in ("a", "b", "c")
    ]


@pytest.mark.parametrize(
    "f",
    [
        op("/", m("a"), m("b")),
        op("*", op("/", m("a"), m("b")), {"fn": "days_in_period"}),
        op("-", m("a"), {"lit": 5}),
        {"fn": "abs", "args": [op("-", m("a"), m("b"))]},
        op("-", op("/", m("a"), m("b")), m("c")),
        m("a"),
    ],
)
def test_anything_but_a_sum_of_metrics_has_no_terms(f: dict) -> None:
    assert terms(f) is None


# --- the spec ----------------------------------------------------------------------------------


def test_area_and_terms_compile_only_when_present(world) -> None:
    records, cat, company = _records(world)
    bare = {
        k: ({x: y for x, y in b.items() if x != "area"} if k[0] == "metric" else b)
        for k, b in records.items()
    }
    spec = compile_records(bare, cat, company, 1)
    assert all("area" not in e for e in spec["metrics"].values())
    nr = spec["metrics"]["net_revenue"]
    assert nr["terms"] == [
        {"metric": "invoiced_value", "sign": 1},
        {"metric": "credit_note_value", "sign": -1},
        {"metric": "rebates", "sign": -1},
    ]
    assert nr["terms_by"]["plant"][0] == {"metric": "line_revenue", "sign": 1}
    assert "terms" not in spec["metrics"]["dso"]  # a ratio has no terms
    assert spec["metrics"]["dso"]["per_period"] is True  # × days in period (M16.2)
    assert "per_period" not in spec["metrics"]["net_revenue"]
    assert "terms" not in spec["metrics"]["invoiced_value"]  # a base metric neither

    body = records[("metric", "net_revenue")]
    with_area = {**bare, ("metric", "net_revenue"): {**body, "area": "Sales"}}
    spec2 = compile_records(with_area, cat, company, 1)
    assert spec2["metrics"]["net_revenue"]["area"] == "Sales"
    # a grouping, never a change of meaning
    for k, e in spec["metrics"].items():
        assert spec2["metrics"][k].get("definition_hash") == e.get("definition_hash")


def test_the_sample_company_carries_areas(world) -> None:
    records, cat, company = _records(world)
    spec = compile_records(records, cat, company, 1)
    got = {k: e.get("area") for k, e in spec["metrics"].items() if e.get("listed")}
    assert got["net_revenue"] == "Sales"
    assert got["dso"] == "Collections"
    assert got["metres_dispatched"] == "Operations"
    assert got["cash_balance"] == "Finance"
    assert got["active_customers"] == "Customers"
    assert all(got.values()), [k for k, v in got.items() if not v]


def test_an_area_change_keeps_the_series_version(world) -> None:
    with world.sf() as s, tenant_scope(world.cid):
        company = world.company(s)
        before = compile_version(s, company).compiled["metrics"]
        rec = next(r for r in service.active(s, ["metric"]) if r.key == "dso")
        service.apply(
            s,
            [service.put("metric", "dso", {**rec.body, "area": "Finance"})],
            actor="o",
            reason="Area changed",
        )
        after = compile_version(s, company).compiled["metrics"]
        s.commit()
    assert after["dso"]["area"] == "Finance"
    assert {k: e.get("definition_hash") for k, e in after.items()} == {
        k: e.get("definition_hash") for k, e in before.items()
    }


def test_a_sum_of_three_reads_every_part() -> None:
    """A `+` with three args used to drop the third in SQL (found in M16.1)."""
    from othor.ontology.compiler.build import _derived_sql

    sql = _derived_sql(
        "x", op("+", m("a"), m("b"), m("c")), "month", None, {"a": "flow", "b": "flow", "c": "flow"}
    )
    assert '"c2"."value"' in sql


# --- Tell Othor AI ("put DSO under Finance") ---------------------------------------------------


def test_tell_othor_ai_sets_an_area_and_refuses_a_bad_one(world) -> None:
    import datetime as dt

    from othor.home import changes

    today = dt.date(2026, 11, 15)
    with world.sf() as s, tenant_scope(world.cid):
        said = "put DSO under Finance"
        inputs = changes.model_inputs(s, said, "metric:dso", today)
        dso = next(r for r in inputs["records"] if r["type"] == "metric" and r["key"] == "dso")
        assert dso["body"]["area"] == "Collections"  # the model sees the current area

        def out(area: str) -> dict:
            return {
                "change": {"op": "put", "type": "metric", "key": "dso", "body": {"area": area}},
                "reply": "DSO moves to Finance.",
            }

        d = changes.check(s, out("Finance"), inputs, said, today)
        assert d.change is not None and d.change["body"]["area"] == "Finance"
        assert d.change["body"]["formula"]  # every other field kept
        _section, before, after = changes.card_line(s, d.change)
        assert (before, after) == ("DSO · Collections", "DSO · Finance")
        eff = changes.effect(s, world.company(s), d.change, today=today)
        assert not eff.error
        assert changes.check(s, out("Top 10"), inputs, said, today).change is None
        assert changes.check(s, out("x" * 41), inputs, said, today).change is None
