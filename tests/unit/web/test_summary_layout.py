# SPDX-License-Identifier: Apache-2.0
"""The owner's Summary layout (v0.16, M16.2): one row per company; move, hide, show, the lead
number and the view, each an owner action with an event; a change that changes nothing writes
nothing; an unknown area or number is refused; one company's layout never shows in another's."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from othor.core.models import Event, SummaryLayout
from othor.core.tenancy import tenant_scope
from othor.web import summary_layout as sl

SHOWN = ["Sales", "Collections", "Finance"]


def _events(s, action):
    return s.scalars(sa.select(Event).where(Event.action == action)).all()


def test_each_change_is_an_event_and_a_no_op_writes_nothing(session_factory, two_companies):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        assert sl.get(s) == {
            "order": [],
            "hidden": [],
            "hidden_numbers": [],
            "leads": {},
            "view": {},
        }
        assert sl.move(s, "Finance", "up", shown=SHOWN, actor="o") is True
        assert sl.get(s)["order"] == ["Sales", "Finance", "Collections"]
        assert sl.move(s, "Sales", "up", shown=["Sales", "Finance"], actor="o") is False
        assert sl.hide(s, "Collections", known=SHOWN, actor="o") is True
        assert sl.hide(s, "Collections", known=SHOWN, actor="o") is False
        assert sl.show(s, "Collections", actor="o") is True
        assert sl.show(s, "Collections", actor="o") is False
        assert sl.set_lead(s, "Sales", "orders", metrics=["net_revenue", "orders"], actor="o")
        assert not sl.set_lead(s, "Sales", "orders", metrics=["net_revenue", "orders"], actor="o")
        view = {"window": "4w", "compare": "year", "show": "abs", "split": ""}
        assert sl.set_view(s, view, actor="o") and not sl.set_view(s, view, actor="o")
        s.commit()
    with session_factory() as s, tenant_scope(cid):
        assert sl.get(s) == {
            "order": ["Sales", "Finance", "Collections"],
            "hidden": [],
            "hidden_numbers": [],
            "leads": {"Sales": "orders"},
            "view": view,
        }
        assert s.scalar(sa.select(sa.func.count()).select_from(SummaryLayout)) == 1
        for action, n in (
            ("summary.reordered", 1),
            ("summary.hidden", 1),
            ("summary.shown", 1),
            ("summary.lead_changed", 1),
            ("summary.view_changed", 1),
        ):
            evs = _events(s, action)
            assert len(evs) == n, action
        (ev,) = _events(s, "summary.lead_changed")
        assert ev.before == {"leads": {}} and ev.after == {"leads": {"Sales": "orders"}}


def test_unknown_areas_and_numbers_are_refused(session_factory, two_companies):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        with pytest.raises(sl.LayoutError):
            sl.move(s, "Mars", "up", shown=SHOWN, actor="o")
        with pytest.raises(sl.LayoutError):
            sl.move(s, "Sales", "sideways", shown=SHOWN, actor="o")
        with pytest.raises(sl.LayoutError):
            sl.hide(s, "Mars", known=SHOWN, actor="o")
        with pytest.raises(sl.LayoutError):
            sl.set_lead(s, "Sales", "dso", metrics=["net_revenue"], actor="o")
        assert s.scalar(sa.select(sa.func.count()).select_from(SummaryLayout)) == 0


def test_one_companys_layout_never_shows_in_another(session_factory, two_companies):
    a, b = two_companies
    with session_factory() as s, tenant_scope(a):
        sl.hide(s, "Sales", known=SHOWN, actor="o")
        s.commit()
    with session_factory() as s, tenant_scope(b):
        assert sl.get(s)["hidden"] == []
        sl.hide(s, "Finance", known=SHOWN, actor="o")
        s.commit()
    with session_factory() as s, tenant_scope(a):
        assert sl.get(s)["hidden"] == ["Sales"]


def test_hide_and_show_a_number(session_factory, two_companies):
    cid, other = two_companies
    with session_factory() as s, tenant_scope(cid):
        assert sl.hide_number(s, "orders", known=["orders", "dso"], actor="o")
        assert not sl.hide_number(s, "orders", known=["orders", "dso"], actor="o")
        sl.hide(s, "Sales", known=SHOWN, actor="o")
        got = sl.get(s)
        assert got["hidden"] == ["Sales"] and got["hidden_numbers"] == ["orders"]
        with pytest.raises(sl.LayoutError):
            sl.hide_number(s, "made_up", known=["orders"], actor="o")
        assert sl.show_number(s, "orders", actor="o")
        assert sl.get(s)["hidden_numbers"] == []
        assert len(_events(s, "summary.hidden")) == 2 and len(_events(s, "summary.shown")) == 1
        s.commit()
    with session_factory() as s, tenant_scope(other):
        assert sl.get(s)["hidden_numbers"] == []
