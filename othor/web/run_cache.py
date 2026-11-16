# SPDX-License-Identifier: Apache-2.0
"""What pages build from a run's stored points, built once per run file (M15.1).

A sealed run's files never change, so a page can keep what it derives from them: the series
index Sweep reads, and the points grouped by metric that the Ontology's tiles read (each tile then
filters its own metric's few points instead of every point of the run). Keyed by path, size and
modification time like `store.read_points_cached`, so a rewritten file is read again; a few runs
are kept, shared by all companies in the process. The engine never reads from here.
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from othor.engine.detectors.base import SeriesIndex
from othor.engine.store import Point, read_points_cached

SIZE = 16
_CACHE: OrderedDict[tuple[str, str, int, int], Any] = OrderedDict()


def _cached[T](kind: str, path: Path, build: Callable[[], T], empty: T) -> T:
    try:
        st = path.stat()
    except FileNotFoundError:
        return empty
    key = (kind, str(path), st.st_mtime_ns, st.st_size)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]  # type: ignore[no-any-return]
    value = build()
    _CACHE[key] = value
    while len(_CACHE) > SIZE:
        _CACHE.popitem(last=False)
    return value


def series_index(path: Path) -> SeriesIndex:
    """The run's series, as the detectors index them (read only by pages)."""
    return _cached("index", path, lambda: SeriesIndex(read_points_cached(path)), SeriesIndex(()))


def by_metric(path: Path) -> dict[str, tuple[Point, ...]]:
    """The run's points grouped by metric, in their stored order."""

    def build() -> dict[str, tuple[Point, ...]]:
        out: dict[str, list[Point]] = defaultdict(list)
        for p in read_points_cached(path):
            out[p.metric].append(p)
        return {k: tuple(v) for k, v in out.items()}

    return _cached("by_metric", path, build, {})


def scope_options(spec: dict[str, Any], path: Path) -> dict[str, list[str]]:
    """Edit a brief's breakdown values (`briefs.edit.scope_options`), from an index of the run's
    month values built once per run file."""
    from othor.briefs import edit

    values = _cached("month_values", path, lambda: edit.month_values(read_points_cached(path)), {})
    return edit.scope_options_from(spec, values)
