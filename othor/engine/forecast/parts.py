# SPDX-License-Identifier: Apache-2.0
"""What drives a total (v0.19, M19.2): each signed part forecast, reconciled to the total.

For a number with terms (M16.1: Net revenue = invoiced value − credit notes − rebates), each part
gets its own v2 forecast; the parts are then scaled so their signed sum equals the total's P50,
the difference shared out in proportion to each part's size, so they always add up. The biggest
mover is the part whose change from its last actual moves the total most. Pure: no model.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _d(v: float) -> str:
    return str(Decimal(repr(float(v))).quantize(Decimal("0.000001")))


def reconcile(total: float, parts: list[tuple[str, int, float]]) -> list[tuple[str, int, float]]:
    """(metric, sign, p50) → the same, scaled so sum(sign × p50) == total exactly (to 1e-6)."""
    signed = sum(s * v for _, s, v in parts)
    size = sum(abs(v) for _, _, v in parts)
    if not parts or size == 0:
        return parts
    diff = total - signed
    return [(m, s, v + s * diff * abs(v) / size) for m, s, v in parts]


def drivers(
    total_p50: float,
    total_last: float,
    parts: list[tuple[str, int, float, float]],  # metric, sign, p50, last actual
    names: dict[str, str],
) -> dict[str, Any]:
    """The reconciled parts for the next period, and the biggest mover of the total."""
    rec = reconcile(total_p50, [(m, s, p) for m, s, p, _ in parts])
    last = {m: a for m, _, _, a in parts}
    rows: list[dict[str, Any]] = []
    for m, s, p in rec:
        move = s * (p - last[m])  # what this part does to the total
        rows.append(
            {
                "metric": m,
                "name": names.get(m, m),
                "sign": s,
                "p50": _d(p),
                "last": _d(last[m]),
                "moves_total_by": _d(move),
            }
        )
    change = total_p50 - total_last
    top = max(rows, key=lambda r: (abs(float(r["moves_total_by"])), r["metric"]), default=None)
    biggest = None
    if top is not None and float(top["moves_total_by"]) != 0:
        part_dir = "up" if float(top["p50"]) > float(top["last"]) else "down"
        biggest = {
            "metric": top["metric"],
            "name": top["name"],
            "part_goes": part_dir,
            "total_goes": "up" if float(top["moves_total_by"]) > 0 else "down",
            "share": _d(float(top["moves_total_by"]) / change) if change else None,
        }
    return {"parts": rows, "biggest_mover": biggest, "adds_up": True}
