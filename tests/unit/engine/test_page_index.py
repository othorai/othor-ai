# SPDX-License-Identifier: Apache-2.0
"""The pages' index of a run's points, stored beside the run (load test, 25 Nov 2026): the same
points, series and breakdown values as built from the series file; a run without the file (sealed
before it existed, or the file deleted) has it built once and written; a rewritten series file is
never served from an old index."""

from __future__ import annotations

import datetime as dt
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest

from othor.briefs import edit
from othor.engine import page_index, store
from othor.engine.detectors.base import SeriesIndex
from othor.engine.store import Point, dec, read_points, write_points
from othor.web import run_cache


def _pts() -> list[Point]:
    out = []
    for i, month in enumerate((7, 8, 9, 10)):
        d = dt.date(2026, month, 1)
        out += [
            Point("net_revenue", "month", "", None, d, dec(f"4620000{i}.123456")),
            Point("net_revenue", "month", "region", "South", d, dec("-0.000001")),
            Point("net_revenue", "month", "region", "Tamil Nādu", d, dec(i)),
            Point("net_revenue", "month", "region", "West", d, None),
            Point("net_revenue", "week", "", None, d, dec("12.5"), reconstructed=True),
            Point("orders", "month", "", None, d, dec(10 + i), complete=i < 3),
            Point("orders", "month", "channel", "Online", d, dec(i), stale=i == 1),
            Point("orders", "month", "channel", "", d, dec(1)),
        ]
    return out


@pytest.fixture
def series(tmp_path: Path) -> Path:
    f = tmp_path / "runs" / "r1" / "outputs" / "series.parquet"
    write_points(f, _pts())
    run_cache.clear()
    return f


def test_the_index_holds_the_same_points_series_and_breakdown_values(series: Path) -> None:
    pts = read_points(series)
    assert page_index.build(series) == page_index.path_for(series)
    assert page_index.path_for(series) == series.parent.parent / page_index.NAME  # not outputs/
    idx = page_index.load(series)

    grouped: dict[str, list[Point]] = defaultdict(list)
    for p in pts:
        grouped[p.metric].append(p)
    assert dict(idx.by_metric) == {k: tuple(v) for k, v in grouped.items()}
    for k, v in grouped.items():  # exact decimals, the same text
        assert [str(p.value) for p in idx.by_metric[k]] == [str(p.value) for p in v]
    assert "missing" not in idx.by_metric and idx.by_metric.get("missing") is None
    assert list(idx.all_points()) == pts  # the file's order, for a step run on a page
    assert run_cache.points(series) == pts

    built = SeriesIndex(pts)
    got = idx.series_index
    assert got.metrics() == built.metrics()
    for k, s in built._s.items():
        mine = got.get(*k)
        assert mine is not None and mine.id == s.id
        assert mine.periods == s.periods and mine.reconstructed == s.reconstructed
        assert np.array_equal(mine.values, s.values, equal_nan=True)
    for m in built.metrics():
        for g in ("week", "month"):
            assert got.breakdowns(m, g) == built.breakdowns(m, g)
            for b in ["", *built.breakdowns(m, g)]:
                assert [s.id for s in got.values_of(m, g, b)] == [
                    s.id for s in built.values_of(m, g, b)
                ]
    assert got.get("orders", "day") is None
    assert idx.month_values() == edit.month_values(pts)


def test_a_run_without_the_file_has_it_built_once(series: Path, monkeypatch) -> None:
    """A run sealed before the index existed (or its file deleted): the first page builds and
    writes it; later pages, in this process or another, only load it."""
    calls = []
    real = page_index._arrays
    monkeypatch.setattr(page_index, "_arrays", lambda *a: calls.append(1) or real(*a))
    assert not page_index.path_for(series).exists()
    first = run_cache.series_index(series)
    assert len(calls) == 1 and page_index.path_for(series).exists()
    stamp = page_index.path_for(series).stat().st_mtime_ns
    run_cache.clear()  # another process, or pushed out of this one
    again = run_cache.series_index(series)
    assert len(calls) == 1 and page_index.path_for(series).stat().st_mtime_ns == stamp
    assert again.metrics() == first.metrics()
    # deleting it is harmless: built again, the same
    page_index.path_for(series).unlink()
    run_cache.clear()
    assert run_cache.by_metric(series)["orders"] == tuple(
        p for p in read_points(series) if p.metric == "orders"
    )
    assert len(calls) == 2 and page_index.path_for(series).exists()


def test_loading_the_file_never_reads_the_points(series: Path, monkeypatch) -> None:
    page_index.build(series)
    store.clear_cache()

    def boom(*_a: object) -> None:
        raise AssertionError("rebuilt from the series file")

    monkeypatch.setattr(store, "_read_file", boom)
    monkeypatch.setattr(page_index, "_arrays", boom)
    assert run_cache.series_index(series).get("net_revenue", "month") is not None
    assert len(run_cache.by_metric(series)["net_revenue"]) == 20
    assert run_cache.scope_options({}, series) == {}


def test_a_rewritten_series_file_is_never_served_from_an_old_index(series: Path) -> None:
    page_index.build(series)
    write_points(series, [Point("orders", "month", "", None, dt.date(2026, 10, 1), dec(7))])
    os.utime(series, ns=(1, 1))  # a different stamp even within one clock tick
    with pytest.raises(page_index.Stale):
        page_index.load(series)
    assert dict(run_cache.by_metric(series)) == {"orders": tuple(read_points(series))}


def test_a_broken_or_foreign_file_is_built_again(series: Path) -> None:
    page_index.path_for(series).write_bytes(b"not an index")
    with pytest.raises(page_index.Stale):
        page_index.load(series)
    assert run_cache.series_index(series).get("orders", "month") is not None
    page_index.load(series)  # written whole this time


def test_no_series_file_is_an_empty_page(tmp_path: Path) -> None:
    missing = tmp_path / "runs" / "r9" / "outputs" / "series.parquet"
    assert page_index.build(missing) is None
    assert run_cache.series_index(missing).metrics() == []
    assert dict(run_cache.by_metric(missing)) == {}


def test_the_cache_keeps_a_bounded_number_of_points(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(run_cache, "POINTS", 40)
    run_cache.clear()
    files = []
    for n in range(3):
        f = tmp_path / "runs" / f"r{n}" / "outputs" / "series.parquet"
        write_points(f, _pts())  # 32 points each
        files.append(f)
        run_cache.series_index(f)
    assert len(run_cache._CACHE) == 1  # only the newest fits
    assert next(iter(run_cache._CACHE))[0] == str(files[-1])


def test_the_worker_keeps_a_companys_newest_index_files_only(tmp_path: Path) -> None:
    """After each seal the company's older index files go (a cache: an older run opened later
    builds its own again), so the data volume doesn't grow by one per night."""
    runs = tmp_path / "runs"
    files = []
    for n in range(5):
        f = runs / f"r{n}" / "outputs" / "series.parquet"
        write_points(f, _pts())
        page_index.build_quietly(f)
        os.utime(page_index.path_for(f), ns=(n + 1, n + 1))  # r4 the newest
        files.append(f)
    page_index.prune(runs)
    kept = [f for f in files if page_index.path_for(f).exists()]
    assert kept == files[-page_index.KEEP :]
    run_cache.clear()
    assert run_cache.series_index(files[0]).get("orders", "month") is not None  # rebuilt
    assert page_index.path_for(files[0]).exists()
