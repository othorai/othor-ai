# SPDX-License-Identifier: Apache-2.0
"""0.28.3 (owner, 19 Nov 2026): what a series means, and so when its history starts again.
Only a change of what its values are restarts a series, and only that series."""

from __future__ import annotations

import copy
from typing import Any

from othor.ontology.compiler.build import BREAKDOWN_NOT_MEANING, meaning_hashes
from othor.ontology.types import BreakdownBody

ZERO = {"lit": 0}
MEANING = {"column", "table", "vocabulary", "alternatives"}


def _world() -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {
        ("metric", "invoiced"): {
            "name": "Invoiced value",
            "table": {"source": "erp_db", "schema": "erp", "table": "invoices"},
            "measure": {"agg": "sum", "expr": {"col": "net_amount"}},
            "date": "invoice_date",
            "breakdowns": ["customer", "region"],
            "evidence": ["invoice_no"],
        },
        ("breakdown", "customer"): {
            "name": "Customer",
            "column": "name",
            "table": "customers",
            "cap": 50,
            "entity": {
                "table": {"source": "erp_db", "schema": "erp", "table": "customers"},
                "metric": "invoiced",
            },
        },
        ("breakdown", "region"): {"name": "Region", "column": "region", "table": "customers"},
    }
    return records, {"metrics": {"invoiced": {}}, "rules": []}


def _h(records: dict[tuple[str, str], dict[str, Any]], spec: dict[str, Any]) -> dict[str, str]:
    return meaning_hashes("invoiced", records, spec)


def test_every_breakdown_field_is_classified() -> None:
    """A new field on a breakdown has to be put on one side: does it change the values or not?"""
    fields = {f.alias or name for name, f in BreakdownBody.model_fields.items()}
    assert fields == MEANING | BREAKDOWN_NOT_MEANING


def test_who_is_active_name_and_cap_change_no_meaning() -> None:
    records, spec = _world()
    before = _h(records, spec)
    after = copy.deepcopy(records)
    after[("breakdown", "customer")]["entity"]["active_values"] = ["active"]
    after[("breakdown", "customer")]["name"] = "Billing customer"
    after[("breakdown", "customer")]["cap"] = 80
    after[("metric", "invoiced")]["evidence"] = ["invoice_no", "invoice_date"]
    assert _h(after, spec) == before


def test_a_breakdown_added_or_dropped_keeps_the_total_and_the_others() -> None:
    records, spec = _world()
    before = _h(records, spec)
    records[("breakdown", "segment")] = {"name": "Segment", "column": "segment"}
    records[("metric", "invoiced")]["breakdowns"] = ["customer", "segment"]
    after = _h(records, spec)
    assert after[""] == before[""] and after["customer"] == before["customer"]
    assert "segment" in after and "region" not in after


def test_a_breakdowns_own_column_restarts_only_that_breakdown() -> None:
    records, spec = _world()
    before = _h(records, spec)
    records[("breakdown", "region")]["column"] = "state"
    after = _h(records, spec)
    assert after["region"] != before["region"]
    assert after[""] == before[""] and after["customer"] == before["customer"]


def test_a_filter_or_measure_changes_every_series() -> None:
    records, spec = _world()
    before = _h(records, spec)
    for edit in (
        lambda m: m.__setitem__("filters", [{"op": ">", "args": [{"col": "net_amount"}, ZERO]}]),
        lambda m: m.__setitem__("measure", {"agg": "count", "expr": {"col": "invoice_no"}}),
    ):
        changed = copy.deepcopy(records)
        edit(changed[("metric", "invoiced")])
        after = _h(changed, spec)
        assert all(after[k] != before[k] for k in before)


def test_an_answer_card_sets_only_its_field_on_the_record_as_it_is_at_apply(world):  # type: ignore[no-untyped-def]
    """A card found at night carried the whole record then; applied later, it sets only the field
    it adds and keeps what the owner changed since (0.28.3)."""
    from othor.core.tenancy import tenant_scope
    from othor.home.answers import _card_change
    from othor.ontology import service

    with world.sf() as s, tenant_scope(world.cid):
        rec = next(iter(service.active(s, ["breakdown"])))
        night = copy.deepcopy(rec.body)
        service.apply(
            s,
            [service.put("breakdown", rec.key, {**rec.body, "name": "Renamed"})],
            actor="o",
            reason="the owner renames it",
        )
        s.flush()
        entity = {"table": {"source": "erp_db", "schema": "erp", "table": "t"}, "metric": "m"}
        card = {
            "change": {
                "type": "breakdown",
                "key": rec.key,
                "merge": ["entity"],
                "body": {**night, "entity": entity},
            }
        }
        ch = _card_change(s, card)
        assert ch is not None
        assert ch["body"]["name"] == "Renamed" and ch["body"]["entity"]["metric"] == "m"
