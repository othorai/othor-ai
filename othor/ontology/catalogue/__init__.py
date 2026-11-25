# SPDX-License-Identifier: Apache-2.0
"""The catalogue of numbers by size band (#76, docs/plans/next-1126.md §3, step 2).

`numbers.yaml` beside this file is the owner's reviewed list: the bands in ₹ crore, the numbers
each band usually watches and how each is built. It holds words and recipes only, never a figure
shown to anyone (every figure is computed by `othor.ontology.size`). Loaded once and checked: a
band that names an unknown number, a build that names an unknown part, or a reason with a number
in it fails loudly, so a bad edit is caught by the tests before a night runs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import yaml

FILE = Path(__file__).with_name("numbers.yaml")
UNITS = ("inr", "percent", "count", "days", "inr_per_metre")
AREAS = ("Sales", "Collections", "Operations", "Finance", "Customers")
LEVEL_ITEMS = ("numbers", "parts", "customers", "breakdowns", "goals")
REVENUE = "revenue"  # the size number itself: found by code, not by `parts`


@dataclass(frozen=True)
class Band:
    key: str
    label: str
    start: Decimal  # ₹ crore, inclusive
    numbers: tuple[str, ...]


@dataclass(frozen=True)
class Entry:
    key: str
    name: str
    home: str
    area: str
    unit: str
    level: str
    same: tuple[str, ...]
    build: dict[str, Any]
    reason: str
    figure: str = ""
    needs: str = ""

    @property
    def kind(self) -> str:
        """top (a share of the largest few) · ratio (take − less ÷ over) · table"""
        if "top" in self.build:
            return "top"
        if "table" in self.build:
            return "table"
        return "ratio"

    def parts(self) -> list[str]:
        """The parts (roles) the build uses, in order, each once."""
        b = self.build
        names = [b.get("of"), b.get("take"), b.get("less"), b.get("over")]
        return list(dict.fromkeys(str(n) for n in names if n))

    def by(self) -> list[str]:
        v = self.build.get("by") or []
        return [v] if isinstance(v, str) else [str(x) for x in v]


@dataclass(frozen=True)
class Catalogue:
    version: int
    sha: str
    rates: dict[str, Decimal]
    rates_as_of: str
    bands: tuple[Band, ...]
    numbers: dict[str, Entry]
    parts: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)

    def band_for(self, crore: Decimal) -> Band:
        out = self.bands[0]
        for b in self.bands:
            if crore >= b.start:
                out = b
        return out

    def band(self, key: str) -> Band | None:
        return next((b for b in self.bands if b.key == key), None)


class CatalogueError(ValueError):
    pass


def _has_number(text: str) -> bool:
    from othor.core.numerals import numerals

    return bool(numerals(text))


def parse(raw: str) -> Catalogue:
    doc = yaml.safe_load(raw) or {}
    parts = {
        str(k): {
            "keys": tuple(str(x) for x in v.get("keys") or []),
            "names": tuple(str(x).lower() for x in v.get("names") or []),
        }
        for k, v in (doc.get("parts") or {}).items()
    }
    numbers: dict[str, Entry] = {}
    for key, v in (doc.get("numbers") or {}).items():
        e = Entry(
            key=str(key),
            name=str(v["name"]),
            home=str(v.get("home") or v["name"]),
            area=str(v["area"]),
            unit=str(v["unit"]),
            level=str(v.get("level") or "numbers"),
            same=tuple(str(x).lower() for x in v.get("same") or []),
            build=dict(v["build"]),
            reason=str(v["reason"]),
            figure=str(v.get("figure") or ""),
            needs=str(v.get("needs") or ""),
        )
        if e.unit not in UNITS:
            raise CatalogueError(f"{key}: unit {e.unit!r} isn't one of {UNITS}")
        if e.area not in AREAS:
            raise CatalogueError(f"{key}: area {e.area!r} isn't one of {AREAS}")
        if e.level not in LEVEL_ITEMS:
            raise CatalogueError(f"{key}: level {e.level!r} isn't one of {LEVEL_ITEMS}")
        if _has_number(e.reason):
            raise CatalogueError(f"{key}: a reason has no numbers (they're computed)")
        for p in e.parts():
            if p != REVENUE and p not in parts:
                raise CatalogueError(f"{key}: the part {p!r} isn't under `parts`")
        if e.kind == "table" and not e.needs:
            raise CatalogueError(f"{key}: a number built from a table says what it `needs`")
        if e.kind != "table" and not e.figure:
            raise CatalogueError(f"{key}: a number built from your numbers has a `figure`")
        if e.kind == "ratio" and not e.build.get("take"):
            raise CatalogueError(f"{key}: a build is top/of/by, take/less/over or table")
        numbers[e.key] = e
    bands: list[Band] = []
    for b in doc.get("bands") or []:
        missing = [n for n in b["numbers"] if n not in numbers]
        if missing:
            raise CatalogueError(f"band {b['key']}: unknown numbers {missing}")
        bands.append(
            Band(str(b["key"]), str(b["label"]), Decimal(str(b["from"])), tuple(b["numbers"]))
        )
    if not bands or bands[0].start != 0:
        raise CatalogueError("the first band starts at 0")
    if [b.start for b in bands] != sorted(b.start for b in bands):
        raise CatalogueError("bands go smallest first")
    rates = doc.get("rates") or {}
    return Catalogue(
        version=int(doc.get("version") or 1),
        sha=hashlib.sha256(raw.encode()).hexdigest()[:12],
        rates={str(k): Decimal(str(v)) for k, v in rates.items() if k != "as_of"},
        rates_as_of=str(rates.get("as_of") or ""),
        bands=tuple(bands),
        numbers=numbers,
        parts=parts,
    )


@cache
def load() -> Catalogue:
    return parse(FILE.read_text(encoding="utf-8"))
