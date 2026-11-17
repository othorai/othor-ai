# SPDX-License-Identifier: Apache-2.0
"""A plug-in record type's part of the spec (Advanced Intelligence Phase 0 §5): compiled under
spec["plugins"][type], so detectors read it and the spec hash includes it. A company without
plug-in records compiles byte for byte as before; a core type can't take a compiler."""

from __future__ import annotations

import pytest

from othor.ontology.compiler import compile_records
from othor.ontology.compiler.api import spec_hash
from othor.ontology.compiler.build import COMPILERS, register_compiler
from tests.unit.ontology.test_compiler import _records


def _links(records, spec):
    return {
        "links": [
            {"signal": b["signal"], "metric": b["metric"], "lag_weeks": b["lag_weeks"]}
            for _k, b in sorted(records.items())
            if b["metric"] in spec["metrics"]
        ]
    }


@pytest.fixture
def hook():
    register_compiler("demo_link", _links)
    yield
    COMPILERS.pop("demo_link", None)


def test_a_core_type_cant_take_a_compiler():
    for core in ("metric", "breakdown", "rule", "weather"):
        with pytest.raises(ValueError):
            register_compiler(core, _links)


def test_without_plugin_records_the_spec_is_unchanged(world, hook):
    records, cat, company = _records(world)
    COMPILERS.pop("demo_link")
    before = spec_hash(compile_records(records, cat, company, 1))
    register_compiler("demo_link", _links)
    doc = compile_records(records, cat, company, 1)
    assert "plugins" not in doc and spec_hash(doc) == before


def test_its_part_is_in_the_spec_and_its_hash(world, hook):
    records, cat, company = _records(world)
    before = spec_hash(compile_records(records, cat, company, 1))
    link = {"signal": "diesel", "metric": "freight", "lag_weeks": 4}
    doc = compile_records({**records, ("demo_link", "diesel_freight"): link}, cat, company, 1)
    assert doc["plugins"] == {"demo_link": {"links": [link]}}
    assert spec_hash(doc) != before
    # a type without a compiler stays out, as before
    doc2 = compile_records({**records, ("demo_other", "x"): {"a": 1}}, cat, company, 1)
    assert spec_hash(doc2) == before
