# SPDX-License-Identifier: Apache-2.0
"""A brief's scope (docs/04 §8): which findings, tiles and watches it covers, applied before
ranking. Scope = the metrics it covers (None: every metric) and breakdown values left out
("Export left out · it has its own brief", 4d3)."""

from __future__ import annotations

from typing import Any


def _metric_ok(spec: dict[str, Any], metric: str | None, metrics: list[str] | None) -> bool:
    if metrics is None or metric is None:
        return True
    fam = (spec.get("metrics", {}).get(metric) or {}).get("family")
    return metric in metrics or (fam is not None and fam in metrics)


def finding_in(spec: dict[str, Any], scope: dict[str, Any], f: dict[str, Any]) -> bool:
    if not _metric_ok(spec, f.get("metric"), scope.get("metrics")):
        return False
    out = scope.get("exclude") or {}
    b, v = f.get("breakdown") or "", f.get("breakdown_value")
    if v is not None and v in (out.get(b) or []):
        return False
    left = {x for vals in out.values() for x in vals}
    # a finding about excluded values only (every entity it names is left out)
    ents = set(f.get("entities") or [])
    return not (ents and ents <= left)


def metric_in(spec: dict[str, Any], scope: dict[str, Any], metric: str | None) -> bool:
    """A watch or goal belongs to a brief (decided 7 Nov 2026): an unscoped brief keeps
    everything; a scoped one only what is about its metrics: the item's metric, or a metric it's
    computed from, is in scope. An item with no metric (a count from a finding) is company-wide,
    so only unscoped briefs show it. Nothing is deleted: it stays in Sweep and the Ontology."""
    metrics = scope.get("metrics")
    if metrics is None:
        return True
    if metric is None:
        return False
    seen: set[str] = set()
    todo = [metric]
    while todo:
        k = todo.pop()
        if k in seen:
            continue
        seen.add(k)
        if _metric_ok(spec, k, metrics):
            return True
        todo += list((spec.get("metrics", {}).get(k) or {}).get("components") or [])
    return False


def watch_in(spec: dict[str, Any], scope: dict[str, Any], w: dict[str, Any]) -> bool:
    return metric_in(spec, scope, (w.get("indicator") or {}).get("metric"))


def goal_in(spec: dict[str, Any], scope: dict[str, Any], g: dict[str, Any]) -> bool:
    return metric_in(spec, scope, g.get("metric"))
