# SPDX-License-Identifier: Apache-2.0
"""What every detector shares (docs/04 §3): the series it reads, the result it returns, and the
registry. A detector is a pure function of a run's outputs, its probes, the spec's detector
config and a seed. It never reads a source, a clock or a model, so replay reproduces it.

Results carry numbers as Decimal strings rounded to fixed places, so outputs stay canonical.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np

from othor.engine.store import Point

P_PLACES = Decimal("1e-12")
PAISA = Decimal("0.01")
STAT_PLACES = Decimal("0.000001")


def dec(x: float | Decimal | None, places: Decimal = STAT_PLACES) -> str | None:
    """A float or Decimal as a canonical decimal string (None stays None)."""
    if x is None:
        return None
    if isinstance(x, float) and not np.isfinite(x):
        return None
    return str(Decimal(str(x)).quantize(places))


def pval(p: float) -> str:
    """p-values keep 12 places; anything below that is written as the floor (1e-12)."""
    return str(max(Decimal(str(float(p))), P_PLACES).quantize(P_PLACES))


def seed_for(spec_hash: str, run_date: dt.date, series_id: str) -> int:
    """sha256(spec hash | run date | series id), never the run id (decided 5 Nov 2026)."""
    h = hashlib.sha256(f"{spec_hash}|{run_date.isoformat()}|{series_id}".encode()).digest()
    return int.from_bytes(h[:8], "big")


@dataclass(frozen=True)
class Series:
    metric: str
    grain: str
    breakdown: str  # '' = total
    breakdown_value: str | None
    periods: tuple[dt.date, ...]
    values: np.ndarray  # float64; NaN where the value is missing
    reconstructed: tuple[bool, ...]

    @property
    def id(self) -> str:
        bv = "" if self.breakdown_value is None else self.breakdown_value
        return f"{self.metric}|{self.grain}|{self.breakdown}|{bv}"

    def label(self) -> str:
        return self.metric if not self.breakdown else f"{self.metric}·{self.breakdown_value}"

    def tail(self, n: int) -> Series:
        return Series(
            self.metric,
            self.grain,
            self.breakdown,
            self.breakdown_value,
            self.periods[-n:],
            self.values[-n:],
            self.reconstructed[-n:],
        )

    @property
    def reconstructed_only(self) -> bool:
        return bool(self.reconstructed) and all(self.reconstructed)


class SeriesIndex:
    """The run's complete, non-stale points, as series (current definition version only)."""

    def __init__(self, points: Iterable[Point]) -> None:
        groups: dict[tuple[str, str, str, str | None], list[Point]] = defaultdict(list)
        for p in points:
            if p.complete and not p.stale:
                groups[(p.metric, p.grain, p.breakdown, p.breakdown_value)].append(p)
        self._s: dict[tuple[str, str, str, str | None], Series] = {}
        for k, ps in groups.items():
            ps.sort(key=lambda p: p.period)
            self._s[k] = Series(
                k[0],
                k[1],
                k[2],
                k[3],
                tuple(p.period for p in ps),
                np.array([np.nan if p.value is None else float(p.value) for p in ps]),
                tuple(p.reconstructed for p in ps),
            )

    def get(
        self, metric: str, grain: str, breakdown: str = "", value: str | None = None
    ) -> Series | None:
        return self._s.get((metric, grain, breakdown, value))

    def values_of(self, metric: str, grain: str, breakdown: str) -> list[Series]:
        return [
            s
            for (m, g, b, _), s in sorted(self._s.items(), key=lambda kv: _key(kv[0]))
            if m == metric and g == grain and b == breakdown
        ]

    def breakdowns(self, metric: str, grain: str) -> list[str]:
        return sorted({b for (m, g, b, _) in self._s if m == metric and g == grain and b})

    def metrics(self) -> list[str]:
        return sorted({k[0] for k in self._s})


def _key(k: tuple[str, str, str, str | None]) -> tuple[str, str, str, str]:
    return (k[0], k[1], k[2], "" if k[3] is None else "\x01" + k[3])


@dataclass
class Result:
    """One test of one detector on one series (or pair, or entity set). `passed` is set later
    by FDR, materiality and suppression; `reason` says why not."""

    detector: str
    version: str
    series: list[str]
    metric: str
    breakdown: str
    breakdown_value: str | None
    window: tuple[dt.date, dt.date]
    statistic: str | None
    p_value: str | None
    effect: str | None
    strong: bool  # above the detector's strong threshold (High confidence when q < 0.001)
    direction: str  # up · down · none
    money: str | None  # ₹ at stake, computed (None: the detector has no money)
    money_basis: str  # the arithmetic, in words with the computed numbers' slots
    entities: list[str] = field(default_factory=list)
    slots: dict[str, Any] = field(default_factory=dict)  # values for templates (all computed)
    evidence: list[dict[str, Any]] = field(default_factory=list)  # ≤ 50 rows
    state_hint: str = "assessed"  # measured · assessed · not_determinable · needs_confirmation
    reasons: list[dict[str, Any]] = field(default_factory=list)  # possible reasons + checks
    reconstructed_only: bool = False
    params: dict[str, Any] = field(default_factory=dict)
    eligible: bool = True
    reason: str | None = None  # why not eligible / why it didn't pass
    q_value: str | None = None
    passed: bool = False
    family: str = ""  # de-duplication across metrics of one family

    def key(self) -> tuple[Any, ...]:
        return (self.detector, tuple(self.series), self.direction, tuple(sorted(self.entities)))

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = plain(self._json())
        return out

    def _json(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "version": self.version,
            "series": list(self.series),
            "metric": self.metric,
            "breakdown": self.breakdown,
            "breakdown_value": self.breakdown_value,
            "window": [self.window[0].isoformat(), self.window[1].isoformat()],
            "statistic": self.statistic,
            "p_value": self.p_value,
            "q_value": self.q_value,
            "effect": self.effect,
            "strong": self.strong,
            "direction": self.direction,
            "money": self.money,
            "money_basis": self.money_basis,
            "entities": sorted(self.entities),
            "slots": self.slots,
            "evidence": self.evidence[:50],
            "state_hint": self.state_hint,
            "reasons": self.reasons,
            "reconstructed_only": self.reconstructed_only,
            "params": self.params,
            "eligible": self.eligible,
            "reason": self.reason,
            "passed": self.passed,
            "family": self.family,
        }


def plain(o: Any) -> Any:
    """numpy scalars → Python (bool, int); floats are refused: results are canonical JSON."""
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, float | np.floating):
        raise TypeError("a float reached a detector result: use dec()")
    if isinstance(o, dict):
        return {k: plain(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [plain(v) for v in o]
    return o


@dataclass
class Context:
    spec: dict[str, Any]
    spec_hash: str
    run_date: dt.date
    series: SeriesIndex
    probes: dict[str, Any]
    # the owner's snoozes and mutes active when the run started (run inputs, M6b)
    holds: dict[str, Any] = field(default_factory=dict)
    # forecasts of record for recent periods and tonight's goal projections (M7)
    forecasts: dict[str, Any] = field(default_factory=dict)
    # plug-in run inputs stored before the manifest (Advanced Intelligence Phase 0 §3)
    run_inputs: dict[str, Any] = field(default_factory=dict)
    # the detector versions this run behaves as (`context.json` `detector_versions`, from #138):
    # a run replays with the rules it was made with; a detector not named runs its current one
    versions: dict[str, str] = field(default_factory=dict)

    def version(self, detector: str, current: str) -> str:
        return str(self.versions.get(detector) or current)

    def config(self, detector: str) -> dict[str, Any]:
        return dict(self.spec.get("detectors", {}).get(detector, {}))

    def metric(self, key: str) -> dict[str, Any]:
        return dict(self.spec["metrics"].get(key, {}))

    def active_metrics(self) -> list[str]:
        return [
            k
            for k in self.spec.get("order", [])
            if self.spec["metrics"].get(k, {}).get("status") == "active"
        ]

    def seed(self, series_id: str) -> int:
        return seed_for(self.spec_hash, self.run_date, series_id)

    def rng(self, series_id: str) -> np.random.Generator:
        return np.random.default_rng(self.seed(series_id))


@dataclass(frozen=True)
class Coverage:
    """What a detector checked tonight (5f, 10i) or why it couldn't run (5a)."""

    detector: str
    status: str  # ran · partly · could_not_run
    checked: int  # series, pairs, entities or thresholds tested
    eligible_total: int
    unit: str  # metrics · pairs · customers · thresholds · checks
    needs: str | None = None  # the record or history it needs ("needs pair", "28 days")
    notes: tuple[str, ...] = ()  # what wasn't tested and why ("Skarfab · paused until 1 Dec")

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = plain(self._json())
        return out

    def _json(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "status": self.status,
            "checked": self.checked,
            "eligible_total": self.eligible_total,
            "unit": self.unit,
            "needs": self.needs,
            "notes": list(self.notes),
        }


Detector = Callable[
    [Context], tuple[list[Result], Coverage | None]
]  # None: a plug-in with nothing to check


@dataclass(frozen=True)
class Spec:
    name: str
    version: str
    label: str
    run: Detector
    needs: tuple[str, ...] = ()  # Ontology records it needs (docs/04 "needs" table)
    enabled: bool = True
    # its Benjamini–Hochberg pool (Advanced Intelligence Phase 0 §4): a plug-in's detectors may
    # test in a pool of their own (`detect.register_pool`), never pooled with the inside tests
    pool: str = "inside"


REGISTRY: dict[str, Spec] = {}


def core() -> dict[str, Spec]:
    """The core's own detectors (the open core's Sweep, the website's count): a plug-in's
    detectors (External Signals' outside_moved) run in their own pool and are counted apart."""
    return {
        k: d for k, d in REGISTRY.items() if d.run.__module__.startswith("othor.engine.detectors.")
    }


def register(spec: Spec) -> Spec:
    REGISTRY[spec.name] = spec
    return spec


def finite(a: np.ndarray) -> np.ndarray:
    return a[np.isfinite(a)]
