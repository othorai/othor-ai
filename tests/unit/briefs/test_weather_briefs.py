# SPDX-License-Identifier: Apache-2.0
"""Each brief's own weather (v0.12 part 1c): the company's scoring over what is in the brief's
scope only (findings by `finding_in`; goals, watches and weekly forecasts by `metric_in`); source
health, the checks and history stay company-wide. An unscoped brief equals the company's weather;
the same inputs give the same bytes; every number in a brief's lines is a computed one."""

from __future__ import annotations

import re
from typing import Any

from othor.briefs import weather
from othor.ontology.compiler.api import canonical
from tests.unit.briefs.test_weather import (
    RUN,
    _days,
    _f,
    _far,
    _goal,
    _outputs,
    _spec,
)

LEADERSHIP = {"id": "b-lead", "number": 1, "scope": {"metrics": None, "exclude": {}}}
ORDERS = {"id": "b-orders", "number": 3, "scope": {"metrics": ["orders"], "exclude": {}}}
NO_EXPORT = {
    "id": "b-domestic",
    "number": 2,
    "scope": {"metrics": None, "exclude": {"region": ["Export"]}},
}


def _company(**kw: Any) -> dict[str, Any]:
    return weather.make(
        _spec(),
        RUN,
        kind="nightly",
        first_brief=False,
        findings=kw.get("findings") or [],
        goals=kw.get("goals") or [],
        outputs_doc=kw.get("outputs") or _outputs(),
        points=_days(),
    )


def _briefs(versions: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return weather.for_briefs(
        _spec(),
        RUN,
        versions,
        kind="nightly",
        first_brief=False,
        findings=kw.get("findings") or [],
        goals=kw.get("goals") or [],
        outputs_doc=kw.get("outputs") or _outputs(),
        points=_days(),
    )


def test_an_unscoped_brief_has_the_companys_weather_byte_for_byte() -> None:
    case = {"findings": [_far("a", "5000000", title="South fell ₹0.5 Cr")]}
    got = _briefs([LEADERSHIP], **case)["briefs"]["b-lead"]
    assert got["version"] == 1 and got["scope"] == {"metrics": None, "exclude": {}}
    assert canonical(got["weather"]) == canonical(_company(**case))


def test_a_brief_on_one_metric_gets_its_own_scene() -> None:
    """Net revenue fell (Worth a look for the company); the Orders brief has nothing in scope."""
    case = {"findings": [_far("a", "5000000", title="South fell ₹0.5 Cr")]}
    doc = _briefs([LEADERSHIP, ORDERS], **case)["briefs"]
    assert doc["b-lead"]["weather"]["scene"] == "light_rain"
    assert doc["b-orders"]["weather"]["scene"] == "clear"
    assert doc["b-orders"]["weather"]["label"] == "All clear"


def test_a_goal_out_of_scope_is_not_the_briefs_storm() -> None:
    goal = _goal("2000000000", "2200000000", "2100000000", by="2026-11-30")
    doc = _briefs([LEADERSHIP, ORDERS], goals=[goal])["briefs"]
    assert doc["b-lead"]["weather"]["scene"] == "thunderstorm"
    assert doc["b-orders"]["weather"]["scene"] != "thunderstorm"


def test_left_out_values_leave_the_briefs_weather() -> None:
    """The Export brief can be stormy while the domestic one is clear (plan, Q1)."""
    export = _f(
        "x",
        "5000000",
        breakdown="region",
        breakdown_value="Export",
        entities=["Export"],
        title="Export fell ₹0.5 Cr",
    )
    doc = _briefs([LEADERSHIP, NO_EXPORT], findings=[export])["briefs"]
    assert doc["b-lead"]["weather"]["scene"] == "light_rain"
    assert doc["b-domestic"]["weather"]["scene"] == "clear"


def test_source_health_stays_company_wide() -> None:
    doc = _briefs([ORDERS], outputs=_outputs(unread=("erp_db",)))["briefs"]
    assert doc["b-orders"]["weather"]["scene"] == "no_signal"


def test_same_inputs_same_bytes_whatever_the_order_of_briefs() -> None:
    case = {"findings": [_far("a", "5000000"), _far("b", "600000")]}
    a = canonical(_briefs([LEADERSHIP, ORDERS, NO_EXPORT], **case))
    b = canonical(_briefs([NO_EXPORT, ORDERS, LEADERSHIP], **case))
    assert a == b
    assert list(_briefs([ORDERS, LEADERSHIP], **case)["briefs"]) == ["b-lead", "b-orders"]


def test_every_number_in_a_briefs_lines_is_a_computed_one() -> None:
    """The brief's today line and its outlook lines repeat computed values only."""
    case = {
        "findings": [_far("a", "5000000", title="South fell ₹0.5 Cr"), _far("b", "600000")],
        "goals": [_goal("2000000000", "2200000000", "2100000000", by="2027-03-31")],
    }
    doc = _briefs([LEADERSHIP, ORDERS, NO_EXPORT], **case)["briefs"]
    for entry in doc.values():
        w = entry["weather"]
        words = canonical(w["why"]) + canonical(w["facts"]) + canonical(case["findings"])
        for n in re.findall(r"\d+(?:\.\d+)?", w["line"]):
            assert n in words, (n, w["line"])
        o = w["outlook"]
        items = canonical([x for k in o["weeks"] for x in k["items"]])
        for line in [o["ahead_line"], *(k["line"] for k in o["weeks"])]:
            for n in re.findall(r"\d+(?:\.\d+)?", line):
                assert n in items or n in canonical(o["weeks"]) or n in words, (n, line)


def test_stored_brief_reads_its_own_entry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from othor.engine.store import RunFiles, write_json

    files = RunFiles(tmp_path, "r1")  # type: ignore[arg-type]
    files.outputs.mkdir(parents=True)
    assert weather.stored_brief(files, "b-orders") is None  # sealed before part 1c
    write_json(files.outputs / weather.BRIEFS, _briefs([LEADERSHIP, ORDERS]))
    got = weather.stored_brief(files, "b-orders")
    assert got is not None and got["scope"]["metrics"] == ["orders"]
    assert weather.stored_brief(files, "b-other") is None
