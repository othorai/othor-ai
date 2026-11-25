# SPDX-License-Identifier: Apache-2.0
"""What pages read from a run's stored points (M15.1; from the load test of 25 Nov 2026, through
the run's page index file).

A sealed run's points are read by pages as the series index Sweep reads, the points grouped by
metric that the Ontology's and the brief's tiles read, and each metric's breakdown values. All
three come from the run's page index (`othor.engine.page_index`): a file the worker writes after
the seal, loaded in milliseconds, making Python objects only for the metrics and series a page
shows. A run sealed before that file existed has it built here once, and written.

A loaded index is kept in this process, newest last, for at most SIZE runs and POINTS points
(shared by all companies): a run pushed out costs one small file read, not a rebuild. Keyed by
path, size and modification time like `store.read_points_cached`, so a rewritten file is read
again. The engine never reads from here.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from othor.engine import page_index
from othor.engine.detectors.base import SeriesIndex
from othor.engine.store import Point

SIZE = 32  # runs; a miss is one small file read since the page index (25 Nov 2026)
POINTS = 2_000_000  # and at most this many points (a run's Points are made as pages ask)
_CACHE: OrderedDict[tuple[str, int, int], page_index.PageIndex] = OrderedDict()
_LOCK = threading.Lock()  # a page's threads share the cache


def clear() -> None:
    with _LOCK:
        _CACHE.clear()


def _index(path: Path) -> page_index.PageIndex | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _LOCK:
        got = _CACHE.get(key)
        if got is not None:
            _CACHE.move_to_end(key)
            return got
    got = page_index.load_or_build(path)
    with _LOCK:
        _CACHE[key] = got
        _CACHE.move_to_end(key)
        while len(_CACHE) > 1 and (
            len(_CACHE) > SIZE or sum(v.size for v in _CACHE.values()) > POINTS
        ):
            _CACHE.popitem(last=False)
    return got


def series_index(path: Path) -> SeriesIndex:
    """The run's series, as the detectors index them (read only by pages)."""
    idx = _index(path)
    return SeriesIndex(()) if idx is None else idx.series_index


def by_metric(path: Path) -> Mapping[str, tuple[Point, ...]]:
    """The run's points grouped by metric, in their stored order."""
    idx = _index(path)
    return {} if idx is None else idx.by_metric


def points(path: Path) -> list[Point]:
    """Every point of the run, in the series file's order, as `store.read_points` reads them
    (a step run on a page: Edit brief's preview)."""
    idx = _index(path)
    return [] if idx is None else list(idx.all_points())


def scope_options(spec: dict[str, Any], path: Path) -> dict[str, list[str]]:
    """Edit a brief's breakdown values (`briefs.edit.scope_options`), from the run's month
    values."""
    from othor.briefs import edit

    idx = _index(path)
    return edit.scope_options_from(spec, {} if idx is None else idx.month_values())
