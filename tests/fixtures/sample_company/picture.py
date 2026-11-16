# SPDX-License-Identifier: Apache-2.0
"""The 1 Nov picture (M7, decided 5 Nov 2026): every Sweep item of the steady-state night in a
stable, rounded form, held in `golden_1nov.json`. Any generator or engine change that moves the
picture (a new finding, a lost suppression, a money figure off by ₹1 L or more) fails CI.

Regenerate only on purpose: `OTHOR_UPDATE_GOLDEN=1 uv run pytest tests/e2e/test_nights.py
tests/integration/test_sweep_sample.py`, then review the diff of the golden file like code.
"""

from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

GOLDEN = Path(__file__).with_name("golden_1nov.json")
LAKH = Decimal(100000)

# (detector, metric) of a planted pattern's finding → its id (docs/11); anything else reads "?"
PATTERNS = {
    ("net_hiding_gross", "net_revenue"): "P1",
    ("changepoint", "receivables_over_60"): "P2",
    ("concentration", "order_book"): "P3",
    ("broken_relationship", "freight"): "P6",
    ("rising_variance", "dispatch_time"): "P7",
    ("cross_source", "dispatch_invoice"): "P10",
}


def lakhs(v: Any) -> str | None:
    if v is None:
        return None
    return str((Decimal(str(v)) / LAKH).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def _pattern(f: dict[str, Any]) -> str:
    if f["detector"] == "missing_records":
        ents = f.get("entities") or []
        if len(ents) > 1:
            return "P5"
        return {"Ravira Exports": "P4", "Skarfab": "Skarfab"}.get(ents[0], "?") if ents else "P5"
    if f["detector"] == "cross_source" and str(f.get("family", "")).startswith("reconcile:mis"):
        return "P16"
    if (f.get("slots") or {}).get("kind") == "goal":
        return "P13"
    return PATTERNS.get((f["detector"], f["metric"]), "?")


def picture(
    findings: list[dict[str, Any]],
    results: list[dict[str, Any]],
    watches: list[dict[str, Any]],
    *,
    resolved: bool = True,
) -> dict[str, Any]:
    def item(f: dict[str, Any]) -> dict[str, Any]:
        return {
            "pattern": _pattern(f),
            "detector": f["detector"],
            "metric": f["metric"],
            "state": f["state"],
            "money_lakh": lakhs(f.get("money")),
        }

    suppressed: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        reason = r.get("reason") or ""
        if not reason.startswith("suppressed by "):
            continue
        slots = r.get("slots") or {}
        suppressed.setdefault(reason.removeprefix("suppressed by "), []).append(
            {
                "detector": r["detector"],
                "metric": r["metric"],
                "grain": slots.get("grain") or r["series"][0].split("|")[1],
                "window": r["window"],
                "money_lakh": lakhs(r.get("money")),
            }
        )
    out: dict[str, Any] = {
        "findings": sorted(
            (item(f) for f in findings if f["status"] == "open"),
            key=lambda x: (x["pattern"], x["detector"], x["metric"]),
        ),
        "suppressed": {
            k: sorted(v, key=lambda x: (x["detector"], x["metric"], x["grain"], x["window"]))
            for k, v in sorted(suppressed.items())
        },
        "watching": sorted(
            ({"label": w["label"], "status": w["status"]} for w in watches),
            key=lambda x: x["label"],
        ),
    }
    if resolved:
        out["resolved"] = sorted(
            (
                {**item(f), "reason": f["resolved"]["reason"].split(" · ")[0]}
                for f in findings
                if f["status"] == "resolved"
            ),
            key=lambda x: (x["pattern"], x["detector"], x["metric"]),
        )
    return out


def check(got: dict[str, Any], scenario: str) -> None:
    """Assert the picture of a scenario ("nights": the e2e nights, "steady": the steady-state
    run on a fixture history) matches the golden file. Both share Findings and Watching; their
    Suppressed money differs because their forecasts of record differ (real nights against
    the fixture's reconstructed ones)."""
    want = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if os.environ.get("OTHOR_UPDATE_GOLDEN") == "1":
        want[scenario] = got
        GOLDEN.write_text(json.dumps(want, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        return
    mine = want[scenario]
    assert set(got) == set(mine), (set(got), set(mine))
    for k in mine:
        assert got[k] == mine[k], (k, json.dumps(got[k], indent=1, ensure_ascii=False))
    for other in want.values():
        for k in ("findings", "watching"):
            assert other[k] == mine[k], k
