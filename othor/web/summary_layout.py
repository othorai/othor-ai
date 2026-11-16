# SPDX-License-Identifier: Apache-2.0
"""The owner's Summary page (v0.16, M16.2): the area order, hidden areas, the lead number picked
per area and the view last chosen, one row per company. Every change is an owner action with an
event (before, after); a change that changes nothing writes nothing. The caller passes what the
page shows now (its areas, an area's numbers), so an unknown area or number is refused."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.events import record_event
from othor.core.models import SummaryLayout

EMPTY: dict[str, Any] = {"order": [], "hidden": [], "hidden_numbers": [], "leads": {}, "view": {}}
NUMBER = "number:"  # a hidden number sits in the same list as the areas, by its key (M16.4)


class LayoutError(ValueError):
    pass


def _row(s: Session) -> SummaryLayout | None:
    return s.scalars(sa.select(SummaryLayout)).first()


def get(s: Session) -> dict[str, Any]:
    r = _row(s)
    if r is None:
        return {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in EMPTY.items()}
    hidden = list(r.hidden or [])
    return {
        "order": list(r.order or []),
        "hidden": [h for h in hidden if not h.startswith(NUMBER)],
        "hidden_numbers": [h[len(NUMBER) :] for h in hidden if h.startswith(NUMBER)],
        "leads": dict(r.leads or {}),
        "view": dict(r.view or {}),
    }


def _raw(s: Session, field: str) -> Any:
    r = _row(s)
    if r is None:
        return EMPTY[field]
    v = getattr(r, field)
    return list(v or []) if field in ("order", "hidden") else dict(v or {})


def _save(s: Session, field: str, value: Any, *, actor: str, action: str) -> bool:
    r = _row(s)
    before = _raw(s, field)
    if before == value:
        return False
    if r is None:
        r = SummaryLayout(order=[], hidden=[], leads={}, view={})
        s.add(r)
    setattr(r, field, value)
    s.flush()
    record_event(
        s,
        actor=actor,
        action=action,
        object_type="summary_layout",
        object_id=r.id,
        before={field: before},
        after={field: value},
    )
    return True


def move(s: Session, area: str, direction: str, *, shown: list[str], actor: str) -> bool:
    """Move an area one place up or down in the order the page shows now."""
    if area not in shown or direction not in ("up", "down"):
        raise LayoutError("That area isn't on your Summary")
    order = list(shown)
    i = order.index(area)
    j = i - 1 if direction == "up" else i + 1
    if not 0 <= j < len(order):
        return False
    order[i], order[j] = order[j], order[i]
    return _save(s, "order", order, actor=actor, action="summary.reordered")


def hide(s: Session, area: str, *, known: list[str], actor: str) -> bool:
    if area not in known:
        raise LayoutError("That area isn't on your Summary")
    hidden = _raw(s, "hidden")
    if area in hidden:
        return False
    return _save(s, "hidden", [*hidden, area], actor=actor, action="summary.hidden")


def show(s: Session, area: str, *, actor: str) -> bool:
    hidden = _raw(s, "hidden")
    if area not in hidden:
        return False
    return _save(s, "hidden", [a for a in hidden if a != area], actor=actor, action="summary.shown")


def hide_number(s: Session, metric: str, *, known: list[str], actor: str) -> bool:
    """Hide one number in the Every number view (owner, 14 Nov 2026)."""
    if metric not in known:
        raise LayoutError("That number isn't on your Summary")
    return hide(s, NUMBER + metric, known=[NUMBER + metric], actor=actor)


def show_number(s: Session, metric: str, *, actor: str) -> bool:
    return show(s, NUMBER + metric, actor=actor)


def set_lead(s: Session, area: str, metric: str, *, metrics: list[str], actor: str) -> bool:
    """The number that leads an area's row; `metrics` are that area's numbers now."""
    if metric not in metrics:
        raise LayoutError("That number isn't in this area")
    leads = _raw(s, "leads")
    return _save(s, "leads", {**leads, area: metric}, actor=actor, action="summary.lead_changed")


def set_view(s: Session, view: dict[str, str], *, actor: str) -> bool:
    """The window, comparison, % or absolute and split the owner last chose (a parsed View)."""
    return _save(s, "view", dict(view), actor=actor, action="summary.view_changed")
