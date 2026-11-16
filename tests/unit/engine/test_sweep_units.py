# SPDX-License-Identifier: Apache-2.0
"""Sweep, pure parts (M6a): seeds come from the spec, the run date and the series, never the
run; the same inputs give the same bytes; a company without the Sweep records gets a
"could not run · needs <record>" line per detector, never an error."""

from __future__ import annotations

import datetime as dt
import hashlib

import numpy as np

from othor.engine.detectors import REGISTRY
from othor.engine.detectors.base import seed_for
from othor.engine.sweep import run_sweep
from tests.detectors.synth import RUN, context, noise, points, spec, weeks


def test_the_seed_is_sha256_of_spec_date_and_series():
    want = int.from_bytes(hashlib.sha256(b"abc|2026-11-01|net_revenue|week||").digest()[:8], "big")
    assert seed_for("abc", dt.date(2026, 11, 1), "net_revenue|week||") == want
    ctx = context(spec(), [])
    a = ctx.rng("x").permutation(50)
    b = context(spec(), []).rng("x").permutation(50)
    assert (a == b).all()
    assert not (a == context(spec(), []).rng("y").permutation(50)).all()
    other_day = seed_for("spec-hash", RUN + dt.timedelta(days=1), "x")
    assert other_day != ctx.seed("x")


def _world():
    w = weeks(80)
    vals = 1_000_000 + 20_000 * noise(7, 80)
    vals[60:] += 300_000  # a step, so something fires
    pts = points("net_revenue", "week", w, vals)
    doc = spec(net_revenue={"name": "Net revenue", "unit": "inr", "kind": "flow"})
    return doc, pts


def test_the_same_inputs_give_the_same_bytes():
    doc, pts = _world()
    a = run_sweep(doc, "h", RUN, [], pts, {}, {})
    b = run_sweep(doc, "h", RUN, [], list(reversed(pts)), {}, {})
    assert a.doc["results_hash"] == b.doc["results_hash"]
    assert a.doc["findings_hash"] == b.doc["findings_hash"]
    assert a.results == b.results and a.results and a.findings == b.findings


def test_a_company_without_the_sweep_records_gets_could_not_run_lines():
    doc, pts = _world()
    doc["breakdowns"].pop("customer")  # no entity breakdown either
    out = run_sweep(doc, "h", RUN, [], pts, {}, {})
    cov = {c["detector"]: c for c in out.doc["coverage"]}
    for spec_ in REGISTRY.values():
        if not spec_.enabled or not spec_.needs:
            continue
        c = cov[spec_.name]
        assert c["status"] == "could_not_run", spec_.name
        assert c["needs"] in spec_.needs, (spec_.name, c["needs"])
    assert np.isfinite(out.doc["tests"])


def test_a_stored_chart_is_canonical_text():
    """Sorted keys, numbers as fixed-precision strings written once; load → dump is identical."""
    import json

    from othor.engine.finding_charts import dump, load
    from othor.web.components_demo import demo_specs

    for chart in demo_specs().values():
        text = dump(chart)
        assert dump(load(text)) == text
        assert text == json.dumps(
            json.loads(text), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        assert not any(isinstance(v, float) for v in _leaves(json.loads(text)))


def _leaves(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _leaves(v)
    elif isinstance(o, list):
        for v in o:
            yield from _leaves(v)
    else:
        yield o
