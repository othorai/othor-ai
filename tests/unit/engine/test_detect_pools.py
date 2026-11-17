# SPDX-License-Identifier: Apache-2.0
"""Detector pools (Advanced Intelligence Phase 0 §4): Benjamini–Hochberg runs once per pool,
each with its own q and materiality. The inside pool is byte-identical to the single pool it
replaced: the golden hash below was taken on the code before pools (17 Nov 2026)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

import pytest

from othor.engine import detect
from othor.engine.detectors.base import Coverage, Result, Spec
from tests.detectors.synth import context, spec

# sha256 of the canonical results of `_world()` through `detect.run`, on the code before pools
INSIDE_GOLDEN = "ae40eaeaa0dbeec1eaf96278210bb72d87bc39cc79d0164e1a62f8529f28c1a6"

W = (dt.date(2026, 10, 1), dt.date(2026, 10, 31))


def _r(det: str, metric: str, p: str | None, money: str | None, **kw: Any) -> Result:
    return Result(
        detector=det,
        version="1",
        series=[f"{metric}|week||"],
        metric=metric,
        breakdown=kw.pop("breakdown", ""),
        breakdown_value=kw.pop("breakdown_value", None),
        window=kw.pop("window", W),
        statistic="1.0",
        p_value=p,
        effect="0.5",
        strong=kw.pop("strong", True),
        direction=kw.pop("direction", "down"),
        money=money,
        money_basis="computed",
        **kw,
    )


def _inside_results() -> list[Result]:
    return (
        [
            _r("changepoint", "net_revenue", "0.00001", "9000000"),
            _r("changepoint", "freight", "0.0004", "800000", window=(dt.date(2026, 9, 1), W[1])),
            _r("changepoint", "orders", "0.004", "100000"),  # below the floor
            _r("changepoint", "returns", "0.02", "9000000"),  # no signal
            _r("changepoint", "stock", None, None, eligible=False, reason="needs 28 days"),
            _r("rising_variance", "net_revenue", "0.0002", "700000", family="rev"),
            _r("rising_variance", "gross_revenue", "0.0003", "600000", family="rev"),
            _r("forecast_breach", "net_revenue", "0.003", "5000000", slots={"kind": "goal"}),
            _r(
                "missing_records",
                "invoiced_value",
                "0.000001",
                "36000000",
                entities=["Ravira Exports"],
            ),
        ]
        + [
            _r(
                "bimodal_average",
                f"m{i}",
                f"0.00{i + 1}",
                "900000",
                breakdown="region",
                breakdown_value=f"r{i}",
            )
            for i in range(8)
        ]
        + [_r("lead_lag", f"quiet{i}", "0.5", "900000") for i in range(10)]
    )


def _world(monkeypatch, extra: dict[str, Spec] | None = None):
    inside = _inside_results()

    def make(name: str, rs: list[Result]):
        def run(ctx):
            return [r for r in rs], Coverage(name, "ran", len(rs), len(rs), "metrics")

        return run

    names = sorted({r.detector for r in inside})
    reg = {n: Spec(n, "1", n, make(n, [r for r in inside if r.detector == n])) for n in names}
    reg.update(extra or {})
    monkeypatch.setattr(detect, "REGISTRY", reg)
    doc = spec(**{m: {"name": m, "unit": "inr"} for m in {r.metric for r in inside}})
    return context(doc, [])


def _hash(results: list[Result]) -> str:
    rows = [r.as_json() for r in results]
    text = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def test_the_inside_pool_keeps_its_bytes(monkeypatch):
    got = detect.run(_world(monkeypatch))
    assert _hash(got.results) == INSIDE_GOLDEN


def _outside(name: str, metric: str, p: str) -> Result:
    return _r(name, metric, p, None, direction="up", strong=False)


@pytest.fixture
def outside_pool():
    detect.register_pool("demo_outside", "0.01", materiality=False)
    yield
    detect.POOLS.pop("demo_outside", None)


def _outside_world(monkeypatch, rs: list[Result]):
    def run(ctx):
        return list(rs), Coverage("demo_moved", "ran", len(rs), len(rs), "signals")

    extra = {"demo_moved": Spec("demo_moved", "1", "Moved", run, pool="demo_outside")}
    return _world(monkeypatch, extra)


def test_a_pool_of_its_own_leaves_the_inside_bytes_alone(monkeypatch, outside_pool):
    rs = [_outside("demo_moved", f"diesel{i}", "0.0001") for i in range(30)]
    got = detect.run(_outside_world(monkeypatch, rs))
    inside = [r for r in got.results if r.detector != "demo_moved"]
    assert _hash(inside) == INSIDE_GOLDEN
    mine = [r for r in got.results if r.detector == "demo_moved"]
    assert {r.params["tests"] for r in mine} == {30}
    assert {r.params["pool"] for r in mine} == {"demo_outside"}
    assert all("pool" not in r.params for r in inside)


def test_a_pool_without_money_passes_without_materiality(monkeypatch, outside_pool):
    rs = [_outside("demo_moved", "diesel", "0.00001"), _outside("demo_moved", "cotton", "0.3")]
    got = detect.run(_outside_world(monkeypatch, rs))
    by = {r.metric: r for r in got.results if r.detector == "demo_moved"}
    assert by["diesel"].passed and by["diesel"].money is None
    assert not by["cotton"].passed and by["cotton"].reason == "no signal"


def test_its_own_q_decides(monkeypatch):
    detect.register_pool("demo_strict", "0.0001", materiality=False)
    try:
        rs = [_outside("demo_moved", "diesel", "0.0005")]

        def run(ctx):
            return list(rs), Coverage("demo_moved", "ran", 1, 1, "signals")

        extra = {"demo_moved": Spec("demo_moved", "1", "Moved", run, pool="demo_strict")}
        got = detect.run(_world(monkeypatch, extra))
        (r,) = [r for r in got.results if r.detector == "demo_moved"]
        assert not r.passed and r.reason.startswith("not significant after FDR")
    finally:
        detect.POOLS.pop("demo_strict", None)


def test_the_inside_pool_is_the_cores():
    with pytest.raises(ValueError):
        detect.register_pool("inside", "0.05")
