# SPDX-License-Identifier: Apache-2.0
"""Pages read a run's points through a small cache (M11a page budget); values are the same
exact Decimals the engine reads, and a rewritten file is read again."""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal
from pathlib import Path

from othor.engine.store import Point, dec, read_points, read_points_cached, write_points


def _pts(v: str) -> list[Point]:
    return [
        Point("net_revenue", "month", "", None, dt.date(2026, 10, 1), dec(v)),
        Point("net_revenue", "month", "region", "South", dt.date(2026, 10, 1), dec("-0.000001")),
        Point("net_revenue", "month", "region", "West", dt.date(2026, 10, 1), None),
    ]


def test_cached_equals_read_and_follows_rewrites(tmp_path: Path) -> None:
    f = tmp_path / "series.parquet"
    write_points(f, _pts("46200000.123456"))
    first = read_points_cached(f)
    assert list(first) == read_points(f)
    assert first[0].value == Decimal("46200000.123456")
    assert first[0].value.as_tuple().exponent == -6  # type: ignore[union-attr]
    assert read_points_cached(f) is first  # served from the cache
    write_points(f, _pts("1.5"))
    os.utime(f, ns=(1, 1))  # a different stamp even within one clock tick
    assert read_points_cached(f)[0].value == Decimal("1.500000")


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_points_cached(tmp_path / "none.parquet") == ()


def test_the_engine_reads_through_the_cache_and_never_sees_an_old_file(tmp_path: Path) -> None:
    """M15.1: the night re-reads the same files many times. A rewrite is a new file (written
    beside it, then moved in place), so even the same size within one clock tick is read again."""
    f = tmp_path / "series.parquet"
    write_points(f, _pts("2.000000"))
    st = f.stat()
    assert read_points(f)[0].value == Decimal("2.000000")
    write_points(f, _pts("3.000000"))  # the same size
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))  # and the same time
    assert f.stat().st_size == st.st_size
    got = read_points(f)
    assert got[0].value == Decimal("3.000000")
    got.append(got[0])  # a caller's list is its own
    assert len(read_points(f)) == 3


def test_the_cache_holds_a_bounded_number_of_points(tmp_path: Path, monkeypatch) -> None:
    from othor.engine import store

    monkeypatch.setattr(store, "CACHE_POINTS", 4)
    store.clear_cache()
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    write_points(a, _pts("1"))
    write_points(b, _pts("2"))
    read_points_cached(a)
    read_points_cached(b)  # 6 points > 4: a is let go, b (the newest) is kept
    assert store.cached_points() == 3
