# SPDX-License-Identifier: Apache-2.0
"""Canonical row hashing and the run hash (othor.engine.store, othor.engine.ledger)."""

import datetime as dt
from decimal import Decimal

from othor.engine.ledger import run_hash
from othor.engine.store import Point, hash_points, read_points, write_points

D = dt.date


def _pts():
    return [
        Point("rev", "month", "", None, D(2026, 10, 1), Decimal("4.62")),
        Point("rev", "month", "region", "South", D(2026, 10, 1), Decimal("-1.4")),
        Point("rev", "month", "region", "", D(2026, 10, 1), None),
        Point("rec", "day", "", None, D(2026, 10, 25), Decimal("1"), True),
    ]


def test_hash_ignores_row_order_and_exact_decimal_spelling():
    a = _pts()
    b = list(reversed(a))
    b[0] = b[0]._replace(value=Decimal("1.000000"))
    assert hash_points(a) == hash_points(b)


def test_hash_sees_every_field():
    base = hash_points(_pts())
    for change in (
        {"value": Decimal("4.620001")},
        {"reconstructed": True},
        {"period": D(2026, 9, 1)},
        {"breakdown_value": "North"},
    ):
        pts = _pts()
        pts[1] = pts[1]._replace(**change)
        assert hash_points(pts) != base, change


def test_empty_breakdown_value_differs_from_the_total():
    total = [Point("rev", "month", "region", None, D(2026, 10, 1), Decimal(1))]
    blank = [Point("rev", "month", "region", "", D(2026, 10, 1), Decimal(1))]
    assert hash_points(total) != hash_points(blank)


def test_parquet_round_trip_keeps_the_hash(tmp_path):
    p = tmp_path / "x.parquet"
    write_points(p, _pts())
    assert hash_points(read_points(p)) == hash_points(_pts())
    assert not list(tmp_path.glob("*.tmp"))


def test_run_hash_chains():
    a = run_hash(None, 1, "dry", "s", "0.1.0", "i", "o")
    b = run_hash(a, 2, "nightly", "s", "0.1.0", "i", "o")
    assert a != b and b != run_hash(None, 2, "nightly", "s", "0.1.0", "i", "o")
