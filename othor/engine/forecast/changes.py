# SPDX-License-Identifier: Apache-2.0
"""What changed since the last brief (v0.19, M19.2): tonight's v2 P50 for each upcoming period
against the previous run's, when it moved meaningfully, and why (from the stored runs).

A move counts when it's more than a quarter of tonight's 80% band and at least 2% of P50. The
reasons, in a fixed order: a new actual period, a known pattern added or changed, a goal added
or changed, a method change, the definition changed. Pure: no model, no clock.
"""

from __future__ import annotations

from typing import Any

BAND_SHARE = 0.25
MIN_PCT = 0.02


def changes(tonight: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, Any]:
    """series → {period, from, to, why} for the series whose next forecast moved."""
    if not prev:
        return {}
    before = {
        (r["series"], r["period"]): r["v2"]
        for r in prev.get("rows", [])
        if r.get("v2") and not r.get("reconstructed")
    }
    out: dict[str, Any] = {}
    seen: set[str] = set()
    for r in tonight.get("rows", []):
        s = r["series"]
        if s in seen or r.get("reconstructed") or not r.get("v2"):
            continue
        seen.add(s)
        old = before.get((s, r["period"]))
        now = r["v2"]
        if not old or now.get("p10") is None:
            continue
        a, b = float(old["p50"]), float(now["p50"])
        band = float(now["p90"]) - float(now["p10"])
        if abs(b - a) <= BAND_SHARE * band or abs(b - a) < MIN_PCT * abs(b):
            continue
        metric = r["metric"]
        why = []
        t_in, p_in = tonight.get("inputs", {}), prev.get("inputs", {})
        if t_in.get("last", {}).get(s) != p_in.get("last", {}).get(s):
            why.append("new_actuals")
        if t_in.get("patterns") != p_in.get("patterns"):
            why.append("known_pattern_changed")
        if t_in.get("goals", {}).get(metric) != p_in.get("goals", {}).get(metric):
            why.append("goal_changed")
        if tonight.get("selection", {}).get(s, {}).get("method") != prev.get("selection", {}).get(
            s, {}
        ).get("method"):
            why.append("method_changed")
        if tonight.get("definitions", {}).get(metric, {}).get("hash") != prev.get(
            "definitions", {}
        ).get(metric, {}).get("hash"):
            why.append("definition_changed")
        out[s] = {"period": r["period"], "from": old["p50"], "to": now["p50"], "why": why}
    return dict(sorted(out.items()))
