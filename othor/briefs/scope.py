# SPDX-License-Identifier: Apache-2.0
"""A brief's scope (docs/04 §8): which findings, tiles and watches it covers, applied before
ranking. Scope = the metrics it covers (None: every metric), the areas it covers (`areas`,
Advanced Intelligence Phase 0 §8: a metric's area or a Discover group's key; absent or None:
every area; "Other numbers" takes the metrics without an area) and breakdown values left out
("Export left out · it has its own brief", 4d3). Metrics and areas both apply."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from othor.ontology.areas import OTHER


def _area_ok(spec: dict[str, Any], metric: str | None, areas: list[str] | None) -> bool:
    if areas is None or metric is None:
        return True
    area = (spec.get("metrics", {}).get(metric) or {}).get("area")
    return (area or OTHER) in areas


def _ok(spec: dict[str, Any], metric: str | None, scope: dict[str, Any]) -> bool:
    return _metric_ok(spec, metric, scope.get("metrics")) and _area_ok(
        spec, metric, scope.get("areas")
    )


def _metric_ok(spec: dict[str, Any], metric: str | None, metrics: list[str] | None) -> bool:
    if metrics is None or metric is None:
        return True
    fam = (spec.get("metrics", {}).get(metric) or {}).get("family")
    return metric in metrics or (fam is not None and fam in metrics)


def finding_in(spec: dict[str, Any], scope: dict[str, Any], f: dict[str, Any]) -> bool:
    if not _ok(spec, f.get("metric"), scope):
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
    if scope.get("metrics") is None and scope.get("areas") is None:
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
        if _ok(spec, k, scope):
            return True
        todo += list((spec.get("metrics", {}).get(k) or {}).get("components") or [])
    return False


def focus(spec: dict[str, Any], version: dict[str, Any]) -> list[str] | None:
    """A focused brief's numbers (Option A, owner 20 Nov 2026): a brief with picked numbers
    (`headline`) and no other scope shows items on its own numbers first. The picked numbers and
    the numbers each is directly computed from (one level), sorted; None when the brief isn't
    focused: the default brief (it keeps the whole company), a brief with no picked numbers, or
    one scoped by its metrics or areas (that scope applies as it always has)."""
    if version.get("default"):
        return None
    sc = version.get("scope") or {}
    if sc.get("metrics") is not None or sc.get("areas") is not None:
        return None
    head = [str(m) for m in version.get("headline") or []]
    if not head:
        return None
    # one level (owner, 20 Nov 2026): a picked number and its direct parts, never their parts in
    # turn, so a Receivables brief (DSO is computed from net revenue) doesn't lead with revenue
    metrics = spec.get("metrics", {})
    seen = set(head)
    for k in head:
        seen.update(str(c) for c in (metrics.get(k) or {}).get("components") or [])
    return sorted(seen)


def in_brief(spec: dict[str, Any], version: dict[str, Any]) -> Callable[[dict[str, Any]], bool]:
    """Whether a finding is one of this brief's: in its scope, and, for a focused brief, about
    one of its numbers, a number they're computed from, or a number computed from them
    (`metric_in`'s walk). A finding with no metric is company-wide: not in a focused brief."""
    sc = version.get("scope") or {}
    nums = focus(spec, version)
    on: dict[str, Any] = {"metrics": nums, "exclude": {}}

    def check(f: dict[str, Any]) -> bool:
        if not finding_in(spec, sc, f):
            return False
        return nums is None or (f.get("metric") is not None and metric_in(spec, on, f["metric"]))

    return check


def watch_in(spec: dict[str, Any], scope: dict[str, Any], w: dict[str, Any]) -> bool:
    return metric_in(spec, scope, (w.get("indicator") or {}).get("metric"))


def goal_in(spec: dict[str, Any], scope: dict[str, Any], g: dict[str, Any]) -> bool:
    return metric_in(spec, scope, g.get("metric"))
