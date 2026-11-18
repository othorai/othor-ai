# SPDX-License-Identifier: Apache-2.0
"""Real-row previews in the Othor AI chat (0.26, M26.3): the query is built by code from a
choice's own names, through the compiler's scope (a scanned column only, never a personal one,
the table's own rules), newest first or most common first, a few rows at most."""

from __future__ import annotations

import pytest

from othor.ontology import preview as pv
from othor.ontology.compiler.render import CompileError, PersonalColumnRejected
from tests.unit.ontology.test_compiler import _records

ERP = {"source": "erp_db", "schema": "erp_db", "table": "customers"}


def _t(records, key):
    return records[("rule", key)]


def test_a_reconcile_card_previews_its_latest_numbers_by_its_date(world) -> None:
    records, cat, _co = _records(world)
    body = _t(records, "dispatch_invoice")
    ask = pv.for_change({"type": "rule", "key": "x", "body": body})
    assert ask is not None and ask.kind == "values" and ask.col == "order_no"
    sql, _d, heads = pv.build(records, cat, ask)
    low = sql.lower()
    assert low.startswith("select") and "order by" in low and "desc" in low
    assert "limit 6" in low and "dispatch_date" in low  # the joined date, by a confirmed rule
    assert heads == ["order no", "dispatch date"]


def test_counts_group_a_status_column_most_common_first(world) -> None:
    records, cat, _co = _records(world)
    ask = pv.Ask("counts", ERP, "status", title="Status in customers")
    sql, _d, heads = pv.build(records, cat, ask)
    low = sql.lower()
    assert "count(*)" in low and "group by" in low and "limit 6" in low
    assert heads == ["status", "rows"]


def test_a_personal_column_is_never_read(world) -> None:
    records, cat, _co = _records(world)
    with pytest.raises(PersonalColumnRejected):
        pv.build(records, cat, pv.Ask("values", ERP, "contact_email"))


def test_a_column_not_in_the_scan_is_refused(world) -> None:
    records, cat, _co = _records(world)
    with pytest.raises(CompileError):
        pv.build(records, cat, pv.Ask("values", ERP, "no_such_column"))


def test_a_pair_or_a_metric_has_no_live_preview() -> None:
    assert pv.for_change({"type": "rule", "body": {"kind": "pair", "over": ["a", "b"]}}) is None
    assert pv.for_change({"type": "metric", "body": {}}) is None
    assert pv.for_change(None) is None


def test_cells_are_formatted_by_code() -> None:
    import datetime as dt
    from decimal import Decimal

    assert pv.cell(None) == "—"
    assert pv.cell(dt.date(2026, 11, 1)) == "1 Nov 2026"
    assert pv.cell(Decimal("1250000")) == "12,50,000"
    assert pv.cell("x" * 80).endswith("…") and len(pv.cell("x" * 80)) == pv.TEXT
