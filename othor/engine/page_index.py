# SPDX-License-Identifier: Apache-2.0
"""The pages' index of a sealed run's points, stored beside the run (load test, 25 Nov 2026).

Pages read a run's `outputs/series.parquet` as the Sweep series index, the points grouped by
metric and the breakdown values of each metric. Built from the file, that took seconds of CPU per
run (every point a Python object with an exact Decimal), so with more companies open than the
web's memory holds, nearly every page view rebuilt one and the web fell behind. This is the same
data as numpy arrays in one file, built once by the worker after the run is sealed; a page loads
it in milliseconds and makes Python objects only for the metric or series it shows.

It is a **derived cache**, nothing more: never part of the ledger, the run's hashes, its inputs
or outputs, or anything `othor replay` compares; the engine never reads it. Deleting it is
harmless: the next page rebuilds it from `series.parquet` once (a run sealed before this file
existed is handled the same way). The file names the series file's size and modification time
it was built from, so a rewritten series file is never served from an old index. It is plain
numpy (`allow_pickle=False`), so loading it runs no code.

Same numbers: each point's value is stored as the exact decimal text and read back as the same
Decimal; the series index stores the same float64 values `SeriesIndex` computes from them.
"""

from __future__ import annotations

import datetime as dt
import gc
import json
import logging
import os
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from othor.engine.detectors.base import Series, SeriesIndex, _key
from othor.engine.store import Point, read_points_cached, read_points_file

log = logging.getLogger("othor.engine.page_index")

VERSION = 1
NAME = f"page_index.v{VERSION}.npz"  # in the run's directory, outside inputs/ and outputs/

_RECON, _COMPLETE, _STALE = 1, 2, 4


@contextmanager
def _no_gc() -> Iterator[None]:
    """Many small objects at once: the cyclic collector would run over them again and again
    (they hold no cycles); it is paused for the few milliseconds it takes."""
    was = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()


def _point(fields: tuple[Any, ...]) -> Point:
    return tuple.__new__(Point, fields)  # all nine fields given: no defaults to fill


def path_for(series: Path) -> Path:
    """The index file of a run, from its `outputs/series.parquet`."""
    return series.parent.parent / NAME


def _stamp(series: Path) -> tuple[int, int] | None:
    try:
        st = series.stat()
    except FileNotFoundError:
        return None
    return (st.st_size, st.st_mtime_ns)


# --- building ---------------------------------------------------------------------------------


def build(series: Path, *, cached: bool = False) -> Path | None:
    """Write the run's index file from its series file (the worker after seal). None when there
    is no series file. `cached`: read the points through the store's cache (the worker has just
    read them); a page reads the file without filling that cache."""
    stamp = _stamp(series)
    if stamp is None:
        return None
    points = read_points_cached(series) if cached else tuple(read_points_file(series))
    with _no_gc():
        arrays = _arrays(points, stamp)
    return _write(series, arrays)


def _write(series: Path, arrays: dict[str, np.ndarray]) -> Path:
    """Written beside it and moved in place, so a reader never sees half a file and two
    processes writing at once both leave a whole one."""
    out = path_for(series)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with tmp.open("wb") as fh:
            np.savez_compressed(fh, **arrays)  # type: ignore[arg-type]
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


KEEP = 3  # a company's newest index files kept on disk; an older run's is built again if opened


def build_quietly(series: Path) -> None:
    """`build`, never failing its caller (the run job): the pages fall back to building it.
    Then the company's older index files go, past the newest KEEP, so the cache never grows
    with the nights (pages mostly open the latest run; an older one rebuilds its own once)."""
    try:
        build(series, cached=True)
        prune(series.parent.parent.parent)
    except Exception:
        log.warning("page index not written", exc_info=True)


def prune(runs_dir: Path, keep: int = KEEP) -> int:
    """Delete all but the `keep` newest index files under a company's runs folder."""
    found = []
    for f in runs_dir.glob(f"*/{NAME}"):
        try:
            found.append((f.stat().st_mtime_ns, f))
        except FileNotFoundError:
            continue
    gone = 0
    for _, f in sorted(found, reverse=True)[keep:]:
        f.unlink(missing_ok=True)
        gone += 1
    return gone


def _arrays(points: tuple[Point, ...], stamp: tuple[int, int]) -> dict[str, np.ndarray]:
    strings: dict[str, int] = {}

    def sid(v: str) -> int:
        got = strings.get(v)
        if got is None:
            got = strings[v] = len(strings)
        return got

    # the points, grouped by metric (metrics sorted), in their stored order within a metric
    order = sorted(range(len(points)), key=lambda i: points[i].metric)  # stable
    pts = [points[i] for i in order]
    m_names: list[int] = []
    m_start: list[int] = []
    last = None
    for i, p in enumerate(pts):
        if p.metric != last:
            m_names.append(sid(p.metric))
            m_start.append(i)
            last = p.metric
    p_cols = np.array(
        [
            (
                sid(p.grain),
                sid(p.breakdown),
                -1 if p.breakdown_value is None else sid(p.breakdown_value),
                p.period.toordinal(),
                (_RECON if p.reconstructed else 0)
                | (_COMPLETE if p.complete else 0)
                | (_STALE if p.stale else 0),
            )
            for p in pts
        ],
        dtype=np.int32,
    ).reshape(-1, 5)
    p_value = np.array([b"" if p.value is None else str(p.value).encode() for p in pts], dtype="S")
    if not len(pts):
        p_value = np.zeros(0, dtype="S1")

    # the series index exactly as SeriesIndex builds it from the same points
    index = SeriesIndex(points)
    keys = sorted(index._s, key=_key)
    s_keys: list[tuple[int, int, int, int]] = []
    s_start: list[int] = []
    periods: list[int] = []
    values: list[np.ndarray] = []
    recon: list[bool] = []
    for k in keys:
        s = index._s[k]
        s_keys.append((sid(k[0]), sid(k[1]), sid(k[2]), -1 if k[3] is None else sid(k[3])))
        s_start.append(len(periods))
        periods.extend(d.toordinal() for d in s.periods)
        values.append(np.asarray(s.values, dtype=np.float64))
        recon.extend(s.reconstructed)
    table = sorted(strings, key=strings.__getitem__)
    meta = {"version": VERSION, "series_size": stamp[0], "series_mtime_ns": stamp[1]}
    return {
        "meta": np.array(json.dumps(meta, sort_keys=True)),
        "strings": np.array(table, dtype="U") if table else np.zeros(0, dtype="U1"),
        "m_names": np.array(m_names, dtype=np.int32),
        "m_start": np.array([*m_start, len(pts)], dtype=np.int64),
        "p_cols": p_cols,
        "p_value": p_value,
        "p_pos": np.array(order, dtype=np.int64),  # each row's place in the series file
        "s_keys": np.array(s_keys, dtype=np.int32).reshape(-1, 4),
        "s_start": np.array([*s_start, len(periods)], dtype=np.int64),
        "s_period": np.array(periods, dtype=np.int32),
        "s_value": np.concatenate(values) if values else np.zeros(0, dtype=np.float64),
        "s_recon": np.array(recon, dtype=bool),
    }


# --- loading ----------------------------------------------------------------------------------


class Stale(Exception):
    """The index file is missing, of another version, or built from another series file."""


def load(series: Path) -> PageIndex:
    """The run's index from its file; `Stale` when it has to be built again."""
    stamp = _stamp(series)
    if stamp is None:
        raise FileNotFoundError(series)
    try:
        with np.load(path_for(series), allow_pickle=False) as z:
            a = {k: z[k] for k in z.files}
    except Exception as e:
        raise Stale(type(e).__name__) from e
    try:
        meta = json.loads(str(a["meta"]))
    except (KeyError, ValueError) as e:
        raise Stale("no meta") from e
    if (
        meta.get("version") != VERSION
        or (
            meta.get("series_size"),
            meta.get("series_mtime_ns"),
        )
        != stamp
    ):
        raise Stale("built from another series file")
    return PageIndex(a)


def load_or_build(series: Path) -> PageIndex:
    """The run's index: from its file, else built (and written, so it is built once)."""
    try:
        return load(series)
    except Stale:
        pass
    stamp = _stamp(series)
    if stamp is None:
        raise FileNotFoundError(series)
    points = tuple(read_points_file(series))
    with _no_gc():
        arrays = _arrays(points, stamp)
    try:
        _write(series, arrays)
        log.info("page index built", extra={"run_dir": series.parent.parent.name})
    except OSError:  # a read-only data folder: this process keeps it in memory only
        log.warning("page index not written", exc_info=True)
    return PageIndex(arrays)


class PageIndex:
    """One run's points for pages, as arrays; Python objects are made per metric or series,
    on first use, and kept."""

    def __init__(self, a: dict[str, np.ndarray]) -> None:
        self._str: list[str] = [str(x) for x in a["strings"].tolist()]
        self._strings = np.array([*self._str, None], dtype=object)
        names = [self._str[i] for i in a["m_names"].tolist()]
        starts = a["m_start"].tolist()
        self._metric_rows = {m: (starts[i], starts[i + 1]) for i, m in enumerate(names)}
        self._p_cols = a["p_cols"]
        self._p_value = a["p_value"]
        self._p_pos = a["p_pos"]
        self._all: tuple[Point, ...] | None = None
        self._points: dict[str, tuple[Point, ...]] = {}
        self._s_keys = a["s_keys"]
        self._s_start = a["s_start"]
        self._s_period = a["s_period"]
        self._s_value = a["s_value"]
        self._s_recon = a["s_recon"]
        self._dates: dict[int, dt.date] = {}
        self._month: dict[tuple[str, str], frozenset[str]] | None = None
        self.by_metric = ByMetric(self)
        self.series_index = StoredSeriesIndex(self)

    @property
    def size(self) -> int:
        """Points in the run (bounds the pages' in-memory cache)."""
        return len(self._p_cols)

    def _date(self, n: int) -> dt.date:
        got = self._dates.get(n)
        if got is None:
            got = self._dates[n] = dt.date.fromordinal(n)
        return got

    def metric_points(self, metric: str) -> tuple[Point, ...] | None:
        got = self._points.get(metric)
        if got is not None:
            return got
        rows = self._metric_rows.get(metric)
        if rows is None:
            return None
        lo, hi = rows
        cols = self._p_cols[lo:hi]
        n = hi - lo
        st = self._strings  # index -1 is None (no breakdown value)
        per = cols[:, 3]
        days, at = np.unique(per, return_inverse=True)
        dates = np.array([self._date(int(d)) for d in days.tolist()], dtype=object)
        fl = cols[:, 4]
        with _no_gc():
            got = tuple(
                map(
                    _point,
                    zip(
                        [metric] * n,
                        st[cols[:, 0]].tolist(),
                        st[cols[:, 1]].tolist(),
                        st[cols[:, 2]].tolist(),
                        dates[at.reshape(-1)].tolist(),
                        [Decimal(v) if v else None for v in self._p_value[lo:hi].astype("U")],
                        ((fl & _RECON) != 0).tolist(),
                        ((fl & _COMPLETE) != 0).tolist(),
                        ((fl & _STALE) != 0).tolist(),
                        strict=True,
                    ),
                )
            )
        self._points[metric] = got
        return got

    def metrics(self) -> list[str]:
        return list(self._metric_rows)

    def all_points(self) -> tuple[Point, ...]:
        """Every point, in the series file's order (`store.read_points`), for the pages that
        run a whole step on them (Edit brief's preview); made once."""
        if self._all is None:
            flat = [p for m in self._metric_rows for p in self.metric_points(m) or ()]
            with _no_gc():
                out: list[Any] = [None] * len(flat)
                for i, pos in enumerate(self._p_pos.tolist()):
                    out[pos] = flat[i]
                self._all = tuple(out)
        return self._all

    def month_values(self) -> dict[tuple[str, str], frozenset[str]]:
        """`briefs.edit.month_values` of the run's points, from the arrays (made once)."""
        if self._month is not None:
            return self._month
        try:
            month = self._str.index("month")
        except ValueError:
            return {}
        out: dict[tuple[str, str], set[str]] = {}
        st = self._str
        for metric, (lo, hi) in self._metric_rows.items():
            cols = self._p_cols[lo:hi]
            mask = (cols[:, 0] == month) & (cols[:, 2] >= 0)
            for b, bv in {(int(b), int(v)) for b, v in cols[mask][:, 1:3].tolist()}:
                if st[b] and st[bv]:
                    out.setdefault((metric, st[b]), set()).add(st[bv])
        self._month = {k: frozenset(v) for k, v in out.items()}
        return self._month


class ByMetric(Mapping[str, tuple[Point, ...]]):
    """The run's points grouped by metric, in their stored order (`run_cache.by_metric`)."""

    def __init__(self, idx: PageIndex) -> None:
        self._idx = idx

    def __getitem__(self, metric: str) -> tuple[Point, ...]:
        got = self._idx.metric_points(metric)
        if got is None:
            raise KeyError(metric)
        return got

    def __iter__(self) -> Iterator[str]:
        return iter(self._idx.metrics())

    def __len__(self) -> int:
        return len(self._idx.metrics())

    def __contains__(self, metric: object) -> bool:
        return metric in self._idx._metric_rows


class StoredSeriesIndex(SeriesIndex):
    """`SeriesIndex` over the stored arrays: the same series, each made when first asked for."""

    def __init__(self, idx: PageIndex) -> None:
        self._idx = idx
        self._s = {}
        st = idx._str
        keys: list[tuple[str, str, str, str | None]] = [
            (st[m], st[g], st[b], None if v < 0 else st[v]) for m, g, b, v in idx._s_keys.tolist()
        ]
        self._rows = {k: i for i, k in enumerate(keys)}  # in `_key` order, as built
        self._keys = keys

    def _series(self, i: int) -> Series:
        k = self._keys[i]
        got = self._s.get(k)
        if got is not None:
            return got
        idx = self._idx
        lo, hi = int(idx._s_start[i]), int(idx._s_start[i + 1])
        got = self._s[k] = Series(
            k[0],
            k[1],
            k[2],
            k[3],
            tuple(idx._date(n) for n in idx._s_period[lo:hi].tolist()),
            idx._s_value[lo:hi].copy(),
            tuple(idx._s_recon[lo:hi].tolist()),
        )
        return got

    def get(
        self, metric: str, grain: str, breakdown: str = "", value: str | None = None
    ) -> Series | None:
        i = self._rows.get((metric, grain, breakdown, value))
        return None if i is None else self._series(i)

    def values_of(self, metric: str, grain: str, breakdown: str) -> list[Series]:
        return [
            self._series(i)
            for i, (m, g, b, _) in enumerate(self._keys)
            if m == metric and g == grain and b == breakdown
        ]

    def breakdowns(self, metric: str, grain: str) -> list[str]:
        return sorted({b for (m, g, b, _) in self._keys if m == metric and g == grain and b})

    def metrics(self) -> list[str]:
        return sorted({k[0] for k in self._keys})
