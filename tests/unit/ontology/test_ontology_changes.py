# SPDX-License-Identifier: Apache-2.0
"""Change with Othor AI (6a3, M10b): a drafted change is checked (types, fields, schema, the
numbers guard with the owner's own figures as written or scaled), and its effect is computed by
compiling the candidate version in memory — nothing is written."""

from __future__ import annotations

import datetime as dt

from othor.core.tenancy import tenant_scope
from othor.home import changes
from othor.ontology import service

TODAY = dt.date(2026, 11, 2)


def _inputs(s, line):
    return changes.model_inputs(s, line, "page", TODAY)


def test_a_goal_target_in_the_owners_words_is_grounded(world):
    with world.sf() as s, tenant_scope(world.cid):
        g = next(r for r in service.active(s, ["goal"]))
        said = "Raise the FY27 revenue goal to ₹65 Cr"
        out = {
            "change": {
                "op": "put",
                "type": "goal",
                "key": g.key,
                "body": {**g.body, "target": "650000000"},
            },
            "reply": "The goal moves.",
        }
        d = changes.check(s, out, _inputs(s, said), said, TODAY)
        assert d.change is not None and d.change["body"]["target"] == "650000000"
        out["change"]["body"]["target"] = "640000000"  # not the owner's number
        assert changes.check(s, out, _inputs(s, said), said, TODAY).change is None


def test_a_new_goal_with_a_numeric_target_is_drafted(world):
    """#66 (15 Nov 2026): "the goal is to reach 200 active customers by march 2027" came back
    from the model as `"target": 200` (a number, in body_json); the record wants "200". It was
    dropped silently while the reply said "This adds a goal". The owner's number is kept as
    text."""
    import json

    with world.sf() as s, tenant_scope(world.cid):
        said = "the goal is to reach 200 active customers by march 2027"
        body = {
            "name": "Active customers target",
            "metric": "active_customers",
            "target": 200,
            "direction": "at_least",
            "by": "2027-03-31",
            "aggregate": "value",
        }
        out = {
            "change": {
                "op": "put",
                "type": "goal",
                "key": "active_customers_target_mar_2027",
                "body_json": json.dumps(body),
            },
            "reply": "This adds a goal for Active customers to reach your target by March 2027.",
        }
        d = changes.check(s, out, _inputs(s, said), said, TODAY)
        assert d.change is not None, d.refused
        assert d.change["body"]["target"] == "200"
        # a number the owner didn't say is still refused, and the reply doesn't claim a change
        out["change"]["body_json"] = json.dumps({**body, "target": 250})
        d = changes.check(s, out, _inputs(s, said), said, TODAY)
        assert d.change is None and d.reply == changes.COULDNT


def test_only_allowed_types_and_fields(world):
    with world.sf() as s, tenant_scope(world.cid):
        m = next(r for r in service.active(s, ["metric"]) if r.key == "net_revenue")
        said = "call it Revenue"
        ok = {
            "change": {
                "op": "put",
                "type": "metric",
                "key": "net_revenue",
                "body": {"name": "Revenue"},
            },
            "reply": "",
        }
        d = changes.check(s, ok, _inputs(s, said), said, TODAY)
        assert d.change["body"]["formula"] == m.body["formula"]  # other fields kept
        bad = {
            "change": {
                "op": "put",
                "type": "metric",
                "key": "net_revenue",
                "body": {"measure": {"agg": "count"}},
            },
            "reply": "",
        }
        assert changes.check(s, bad, _inputs(s, said), said, TODAY).change is None
        table = {"change": {"op": "put", "type": "table_use", "key": "x", "body": {}}, "reply": ""}
        assert changes.check(s, table, _inputs(s, said), said, TODAY).change is None
        retire = {"change": {"op": "retire", "type": "metric", "key": "net_revenue"}, "reply": ""}
        assert changes.check(s, retire, _inputs(s, said), said, TODAY).change is None


def test_the_effect_is_computed_without_writing(world):
    with world.sf() as s, tenant_scope(world.cid):
        n = service.current_number(s)
        m = next(r for r in service.active(s, ["metric"]) if r.key == "receivables_open")
        ch = {
            "op": "put",
            "type": "metric",
            "key": m.key,
            "body": {**m.body, "breakdowns": ["customer"]},
        }
        e = changes.effect(s, world.company(s), ch)
        assert "Open receivables" in e.metrics and e.error is None
        pat = {
            "op": "put",
            "type": "known_pattern",
            "key": "kochi_start",
            "body": {
                "name": "Kochi plant ramp-up",
                "window": {"kind": "range", "start": "2026-12-01", "end": "2027-01-31"},
                "plant": "Kochi",
            },
        }
        e2 = changes.effect(s, world.company(s), pat)
        assert e2.patterns == ["+ Kochi plant ramp-up"]
        assert "Sweep won't flag: Kochi plant ramp-up." in changes.effect_lines(e2)
        assert service.current_number(s) == n  # nothing written


def test_a_source_that_cant_be_reached_gives_values_after_tonight(world):
    with world.sf() as s, tenant_scope(world.cid):
        m = next(r for r in service.active(s, ["metric"]) if r.key == "receivables_open")
        ch = {
            "op": "put",
            "type": "metric",
            "key": m.key,
            "body": {**m.body, "breakdowns": ["customer"]},
        }

        def down(*_a):
            raise ConnectionError("unreachable")

        e = changes.effect(s, world.company(s), ch, preview=down, today=TODAY)
        assert e.note == changes.AFTER_TONIGHT and e.metrics
