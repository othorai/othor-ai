# SPDX-License-Identifier: Apache-2.0
"""ChartSpec v1 (docs/12): a library-neutral, JSON-serialisable description of one chart.

Values are copied from computed results with their provenance; the renderers never compute.
`role` decides colour; sizes and styling belong to the renderer.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Literal

SPEC_VERSION = 1
KINDS = (
    "sparkline",
    "line",
    "step",
    "bars_contribution",
    "waterfall",  # a total, each part's move, the next total (the Test tab, M15.3)
    "bars_share",
    "gap_block",
    "scatter",
    "dumbbell",
    "range_bar",  # a forecast's likely range on a track, its expected value, a goal (M19.5)
)
ROLES = ("positive", "negative", "neutral", "muted", "edge")
Kind = Literal[
    "sparkline",
    "line",
    "step",
    "bars_contribution",
    "waterfall",
    "bars_share",
    "gap_block",
    "scatter",
    "dumbbell",  # two series over the same categories: each row joins its two values
    "range_bar",  # categories low, high, expected (labels: their text); a threshold: the goal
]
# edge: just outside its band, dark with a ring (M15.3)
Role = Literal["positive", "negative", "neutral", "muted", "edge"]
Point = tuple[str, float]


class SpecError(ValueError):
    pass


def _points(xs: tuple[Point, ...] | list[Point]) -> tuple[Point, ...]:
    """Canonical points: (ISO date, float), so JSON and SVG bytes don't depend on int/float."""
    return tuple((str(a), float(b)) for a, b in xs)


@dataclass(frozen=True)
class Unit:
    type: Literal["currency", "percent", "days", "count", "ratio", "metres", "ms", "seconds"] = (
        "count"
    )
    code: str | None = None
    scale: Literal["cr", "l", "b", "m", "k", "none"] = (
        "none"  # crore, lakh; $ billions, millions, thousands
    )
    decimals: int = 0


@dataclass(frozen=True)
class Period:
    grain: Literal["day", "week", "month", "quarter", "year"] = "month"
    from_: str | None = None
    to: str | None = None
    tz: str = "Asia/Kolkata"


@dataclass(frozen=True)
class Axis:
    type: Literal["time", "category"] = "time"
    label: str | None = None


@dataclass(frozen=True)
class YAxis:
    label: str | None = None
    zero_based: bool = False
    # a faint line at 0 and signed ticks: a chart of changes (Summary's % and ABS, #65)
    zero_line: bool = False


@dataclass(frozen=True)
class Series:
    id: str
    name: str
    role: Role
    points: tuple[Point, ...]
    style: Literal["line", "bar", "area", "dots", "smooth", "dotted"] = (
        "line"  # smooth, dotted: Summary (M16.3)
    )
    emphasis: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", _points(self.points))


@dataclass(frozen=True)
class Band:
    series_id: str
    level: float
    lower: tuple[Point, ...]
    upper: tuple[Point, ...]
    source: str = "forecast"
    forecast_run_id: str | None = None
    role: Role = "muted"  # forecast band: muted grey; a spread band (p10–p90): negative;
    # What's ahead's likely range: positive (cyan, as the range bar)
    label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lower", _points(self.lower))
        object.__setattr__(self, "upper", _points(self.upper))


@dataclass(frozen=True)
class Annotation:
    type: Literal["vline", "region", "threshold", "point"]
    at: str | None = None
    from_: str | None = None
    to: str | None = None
    label: str | None = None
    role: Role | None = None
    style: Literal["solid", "dashed"] | None = None
    value: float | None = None


@dataclass(frozen=True)
class Category:
    key: str
    value: float
    role: Role
    total: bool = False
    label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", float(self.value))


@dataclass(frozen=True)
class ChartSpec:
    id: str
    kind: Kind
    title: str
    alt_text: str
    unit: Unit = field(default_factory=Unit)
    period: Period = field(default_factory=Period)
    x: Axis = field(default_factory=Axis)
    y: YAxis = field(default_factory=YAxis)
    series: tuple[Series, ...] = ()
    band: Band | None = None
    annotations: tuple[Annotation, ...] = ()
    categories: tuple[Category, ...] = ()
    provenance: dict[str, str] = field(default_factory=dict)
    explore: dict[str, Any] = field(default_factory=dict)
    spec_version: int = SPEC_VERSION

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise SpecError(f"unknown chart kind: {self.kind}")
        if not self.alt_text:
            raise SpecError("alt_text is required (email alt text, screen readers)")
        roles = [s.role for s in self.series] + [c.role for c in self.categories]
        roles += [a.role for a in self.annotations if a.role]
        roles += [self.band.role] if self.band else []
        bad = [r for r in roles if r not in ROLES]
        if bad:
            raise SpecError(f"unknown role(s): {bad}")
        if self.band and self.band.series_id not in {s.id for s in self.series}:
            raise SpecError("band refers to a series that isn't in the spec")
        if self.kind == "dumbbell":
            keys = [[k for k, _ in s.points] for s in self.series]
            if len(keys) != 2 or keys[0] != keys[1] or not keys[0]:
                raise SpecError("a dumbbell needs two series over the same categories")

    # JSON ------------------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d = _rename(asdict(self), out=True)
        # a field added after v1 is written only when set, so every chart stored before it keeps
        # its exact bytes (stored issues, goldens, replay); `zero_line` (#65, 15 Nov 2026)
        if not d["y"].get("zero_line"):
            d["y"].pop("zero_line", None)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChartSpec:
        d = _rename(d, out=False)
        if d.get("spec_version", SPEC_VERSION) != SPEC_VERSION:
            raise SpecError(f"unsupported spec_version {d.get('spec_version')}")
        pts = lambda xs: tuple((str(a), float(b)) for a, b in xs)  # noqa: E731
        return cls(
            id=d["id"],
            kind=d["kind"],
            title=d["title"],
            alt_text=d["alt_text"],
            unit=Unit(**d.get("unit", {})),
            period=Period(**d.get("period", {})),
            x=Axis(**d.get("x", {})),
            y=YAxis(**d.get("y", {})),
            series=tuple(Series(**{**s, "points": pts(s["points"])}) for s in d.get("series", [])),
            band=Band(
                **{**d["band"], "lower": pts(d["band"]["lower"]), "upper": pts(d["band"]["upper"])}
            )
            if d.get("band")
            else None,
            annotations=tuple(Annotation(**a) for a in d.get("annotations", [])),
            categories=tuple(Category(**c) for c in d.get("categories", [])),
            provenance=dict(d.get("provenance", {})),
            explore=dict(d.get("explore", {})),
        )

    @classmethod
    def from_json(cls, text: str) -> ChartSpec:
        return cls.from_dict(json.loads(text))


def _rename(obj: Any, *, out: bool) -> Any:
    """Python can't name a field `from`; the JSON form uses `from` (docs/12)."""
    a, b = ("from_", "from") if out else ("from", "from_")
    if isinstance(obj, dict):
        return {(b if k == a else k): _rename(v, out=out) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_rename(v, out=out) for v in obj]
    return obj


__all__ = [
    "Annotation",
    "Axis",
    "Band",
    "Category",
    "ChartSpec",
    "Period",
    "Series",
    "SpecError",
    "Unit",
    "YAxis",
    "fields",
]
