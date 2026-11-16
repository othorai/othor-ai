# SPDX-License-Identifier: Apache-2.0
"""The state of the business's thresholds as an Ontology record (v0.12 part 1b): typed and
validated like any record, compiled into the spec's `weather` block only when one exists (so a
company without one compiles the same spec as before), and read by the weather as an override."""

from __future__ import annotations

import pytest

from othor.briefs import weather
from othor.ontology.compiler import compile_records
from othor.ontology.compiler.api import spec_hash
from othor.ontology.types import validate_body
from tests.unit.ontology.test_compiler import _records


def test_the_record_is_typed() -> None:
    assert validate_body("weather", {"material_share": "0.02", "near_days": 21}) == {
        "material_share": "0.02",
        "near_days": 21,
    }
    for bad in ({"material_share": "2"}, {"near_days": 0}, {"colour": "blue"}):
        with pytest.raises(ValueError):
            validate_body("weather", bad)
    # every field is a threshold the weather reads
    from othor.ontology.types import WeatherBody

    assert set(WeatherBody.model_fields) == set(weather.DEFAULTS)


def test_compiled_into_the_spec_only_when_present(world) -> None:
    records, cat, company = _records(world)
    before = compile_records(records, cat, company, 1)
    assert "weather" not in before
    body = validate_body("weather", {"material_share": "0.02"})
    spec = compile_records({**records, ("weather", "thresholds"): body}, cat, company, 1)
    assert spec["weather"] == {"material_share": "0.02"}
    assert spec_hash(spec) != spec_hash(before)
    assert weather.settings(spec)["material_share"] == "0.02"
    assert weather.settings(before)["material_share"] == weather.DEFAULTS["material_share"]
