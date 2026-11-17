# SPDX-License-Identifier: Apache-2.0
"""Where a run's data lives (docs/02 "Storage"): `runs/<run_id>/inputs` (what was read, the
replay snapshot) and `runs/<run_id>/outputs` (what the run computed) as Parquet and canonical
JSON, plus the company's `series` table in `analytics.duckdb`, rewritten when a run seals.

Values are exact: DECIMAL(24,6) in DuckDB and Parquet, `Decimal` quantised to 6 places in Python,
never floats. Rows are hashed in one canonical order, so a hash depends only on the rows.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

Q = Decimal("0.000001")


def dec(v: Any) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(v)).quantize(Q)


class Point(NamedTuple):
    """One value of one series. `breakdown` '' is the total (and `breakdown_value` None)."""

    metric: str
    grain: str
    breakdown: str
    breakdown_value: str | None
    period: dt.date
    value: Decimal | None
    reconstructed: bool = False
    complete: bool = True
    stale: bool = False

    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.metric,
            self.grain,
            self.breakdown,
            "" if self.breakdown_value is None else "\x01" + self.breakdown_value,
            self.period,
        )

    def key(self) -> tuple[str, str, str, str | None, dt.date]:
        return (self.metric, self.grain, self.breakdown, self.breakdown_value, self.period)


INPUT_FIELDS = (
    "metric",
    "grain",
    "breakdown",
    "breakdown_value",
    "period",
    "value",
    "reconstructed",
)
OUTPUT_FIELDS = (*INPUT_FIELDS, "complete", "stale")


def canonical_line(p: Point, fields: Sequence[str]) -> str:
    parts = []
    for f in fields:
        v = getattr(p, f)
        if v is None:
            parts.append("\\N")
        elif isinstance(v, bool):
            parts.append("1" if v else "0")
        elif isinstance(v, dt.date):
            parts.append(v.isoformat())
        elif isinstance(v, Decimal):
            parts.append(str(v.quantize(Q)))
        else:
            parts.append(str(v).replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n"))
    return "\t".join(parts)


def sort_points(points: Iterable[Point]) -> list[Point]:
    return sorted(points, key=Point.sort_key)


def hash_points(points: Iterable[Point], fields: Sequence[str] = INPUT_FIELDS) -> str:
    h = hashlib.sha256()
    for p in sort_points(points):
        h.update(canonical_line(p, fields).encode())
        h.update(b"\n")
    return h.hexdigest()


# --- Parquet through DuckDB (no new dependency) ---------------------------------------------

_DDL = (
    "metric VARCHAR, grain VARCHAR, breakdown VARCHAR, breakdown_value VARCHAR, period DATE, "
    "value DECIMAL(24,6), reconstructed BOOLEAN, complete BOOLEAN, stale BOOLEAN"
)


def _load(con: Any, table: str, points: Iterable[Point]) -> None:
    import pandas as pd

    con.execute(f"CREATE TABLE {table} ({_DDL})")
    rows = [
        (
            p.metric,
            p.grain,
            p.breakdown,
            p.breakdown_value,
            p.period,
            None if p.value is None else str(p.value),
            p.reconstructed,
            p.complete,
            p.stale,
        )
        for p in sort_points(points)
    ]
    if not rows:
        return
    df = pd.DataFrame(rows, columns=[*OUTPUT_FIELDS[:6], "reconstructed", "complete", "stale"])
    df["value"] = df["value"].astype("string")
    con.register("incoming", df)
    con.execute(
        f"INSERT INTO {table} SELECT metric, grain, breakdown, breakdown_value, "  # noqa: S608
        "CAST(period AS DATE), CAST(value AS DECIMAL(24,6)), reconstructed, complete, stale "
        "FROM incoming"
    )
    con.unregister("incoming")


def write_points(path: Path, points: Iterable[Point]) -> int:
    import duckdb

    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        _load(con, "pts", points)
        n = con.execute("SELECT count(*) FROM pts").fetchone()
        tmp = path.with_suffix(".tmp")
        con.execute(
            f"COPY (SELECT * FROM pts ORDER BY metric, grain, breakdown, "  # noqa: S608
            f"breakdown_value NULLS FIRST, period) TO '{_q(tmp)}' (FORMAT parquet)"
        )
        tmp.replace(path)  # a re-run step overwrites its own file, atomically
        return int(n[0]) if n else 0
    finally:
        con.close()


def _exact(v: Any) -> Decimal | None:
    """DuckDB's DECIMAL(24,6) already is a Decimal with 6 places: keep it (same value as
    `dec`, without a str round trip per point; M11a page budget)."""
    if isinstance(v, Decimal) and v.as_tuple().exponent == -6:
        return v
    return dec(v)


_CACHE: dict[Path, tuple[tuple[int, int, int], tuple[Point, ...]]] = {}
CACHE_FILES = 16  # M15.1: several companies and runs no longer push each other out
CACHE_POINTS = 3_000_000  # M15.1: and never more points than this, whatever the files


def clear_cache() -> None:
    _CACHE.clear()


def cached_points() -> int:
    return sum(len(v[1]) for v in _CACHE.values())


def read_points_cached(path: Path) -> tuple[Point, ...]:
    """A file's points, kept for the last few files read (pages since M11a, the night since
    M15.1, which re-reads the same files many times). Keyed by path, inode, size and
    modification time: `write_points` always writes a new file and moves it in place, so a
    rewritten file is never served from the cache."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return ()
    stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
    hit = _CACHE.pop(path, None)
    pts = hit[1] if hit is not None and hit[0] == stamp else tuple(_read_file(path))
    _CACHE[path] = (stamp, pts)  # the newest last
    while len(_CACHE) > 1 and (len(_CACHE) > CACHE_FILES or cached_points() > CACHE_POINTS):
        _CACHE.pop(next(iter(_CACHE)))
    return pts


def read_points(path: Path) -> list[Point]:
    """A file's points as a list of the caller's own (the points themselves are immutable)."""
    return list(read_points_cached(path))


def _read_file(path: Path) -> list[Point]:
    import duckdb

    if not path.exists():
        return []
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT metric, grain, breakdown, breakdown_value, period, value, "  # noqa: S608
            f"reconstructed, complete, stale FROM read_parquet('{_q(path)}')"
        ).fetchall()
    finally:
        con.close()
    return [
        Point(r[0], r[1], r[2], r[3], _date(r[4]), _exact(r[5]), bool(r[6]), bool(r[7]), bool(r[8]))
        for r in rows
    ]


def publish_series(analytics: Path, points_file: Path) -> None:
    """The company's current series: the last sealed run's outputs (one short write)."""
    import duckdb

    analytics.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(analytics))
    try:
        con.execute(
            "CREATE OR REPLACE TABLE series AS SELECT * FROM "  # noqa: S608
            f"read_parquet('{_q(points_file)}')"
        )
    finally:
        con.close()


def _q(p: Path) -> str:
    return str(p).replace("'", "''")


def _date(v: Any) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


# --- canonical JSON files -------------------------------------------------------------------


def write_json(path: Path, doc: Any) -> bytes:
    from othor.ontology.compiler.api import canonical

    data = canonical(doc).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data


def read_json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


RUN_INPUT_PREFIX = "run_input_"  # a plug-in's stored run input (Advanced Intelligence Phase 0)


def run_input_path(files: RunFiles, name: str) -> Path:
    return files.inputs / f"{RUN_INPUT_PREFIX}{name}.json"


def run_input_files(files: RunFiles) -> list[tuple[str, Path]]:
    return [
        (p.stem.removeprefix(RUN_INPUT_PREFIX), p)
        for p in sorted(files.inputs.glob(f"{RUN_INPUT_PREFIX}*.json"))
    ]


def with_run_inputs(context: dict[str, Any], files: RunFiles) -> dict[str, Any]:
    """The run's context with its stored plug-in inputs (the run, replay and the brief step
    read the same files); unchanged when there are none."""
    got = {name: read_json(p) for name, p in run_input_files(files)}
    return {**context, "run_inputs": got} if got else context


class RunFiles:
    """The layout of one run's directory."""

    def __init__(self, runs_dir: Path, run_id: Any) -> None:
        self.root = runs_dir / str(run_id)
        self.inputs = self.root / "inputs"
        self.outputs = self.root / "outputs"

    def source_points(self, source: str) -> Path:
        return self.inputs / f"series_{_safe(source)}.parquet"

    def backfill_points(self, source: str) -> Path:
        return self.inputs / f"backfill_{_safe(source)}.parquet"

    def archive_points(self, source: str) -> Path:
        """Earlier series versions of the source's snapshot metrics, kept for History (never
        read by compute or the detectors); each point's metric is `<metric>@<definition>`."""
        return self.inputs / f"archive_{_safe(source)}.parquet"

    def source_meta(self, source: str) -> Path:
        return self.inputs / f"source_{_safe(source)}.json"

    @property
    def series(self) -> Path:
        return self.outputs / "series.parquet"

    @property
    def baselines(self) -> Path:
        return self.outputs / "baselines.json"

    @property
    def questions(self) -> Path:
        return self.outputs / "questions.json"

    @property
    def outputs_json(self) -> Path:
        return self.outputs / "outputs.json"

    @property
    def manifest(self) -> Path:
        return self.inputs / "manifest.json"


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
