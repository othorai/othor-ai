# SPDX-License-Identifier: Apache-2.0
"""An open finding stays open while its own evidence holds (#142, 25 Nov 2026).

Benjamini–Hochberg's q for a finding depends on how many tests ran that night: adding
breakdowns raised the count, q moved from 0.0076 to 0.0108 with p unchanged, and open findings
closed as "Cleared by data". Now a finding opens at q ≤ 0.01 (the spec's `fdr_q`) and, once open,
stays open while q ≤ 0.05 (`detect.KEEP_Q`). The rule is a run input (the context's `fdr_keep`),
so a run sealed before it replays unchanged. A finding that does fall under the bar says why in
plain words, never "Cleared by data"."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from othor.engine import detect, findings
from othor.engine.detectors.base import Coverage, Result, Spec
from othor.engine.sweep import carry_context
from tests.detectors.synth import context, spec

W = (dt.date(2026, 10, 1), dt.date(2026, 10, 31))
D1, D2 = dt.date(2026, 11, 1), dt.date(2026, 11, 2)
KEEP = {"q": "0.05"}
KEEP_Q = Decimal("0.05")


def _r(metric: str, p: str, det: str = "changepoint") -> Result:
    return Result(
        detector=det,
        version="1",
        series=[f"{metric}|week||"],
        metric=metric,
        breakdown="",
        breakdown_value=None,
        window=W,
        statistic="1.0",
        p_value=p,
        effect="0.5",
        strong=True,
        direction="down",
        money="9000000",
        money_basis="computed",
    )


def _night(monkeypatch, p: str, quiet: int) -> Any:
    """One night: the Nashik step at raw p `p`, among `quiet` other tests that don't fire."""
    rs = [_r("nashik_rework", p)] + [_r(f"quiet{i}", "0.5", "lead_lag") for i in range(quiet)]

    def make(name: str):
        mine = [r for r in rs if r.detector == name]

        def run(ctx: Any) -> tuple[list[Result], Coverage]:
            return list(mine), Coverage(name, "ran", len(mine), len(mine), "metrics")

        return run

    reg = {n: Spec(n, "1", n, make(n)) for n in ("changepoint", "lead_lag")}
    monkeypatch.setattr(detect, "REGISTRY", reg)
    doc = spec(**{m: {"name": m, "unit": "inr"} for m in {r.metric for r in rs}})
    return context(doc, [])


def _step(results: list[Result]) -> Result:
    return next(r for r in results if r.metric == "nashik_rework")


def _open(fs: list[dict[str, Any]]) -> frozenset[str]:
    return frozenset(f["fingerprint"] for f in fs if f["status"] in ("open", *findings.HELD))


def _opened(monkeypatch) -> list[dict[str, Any]]:
    # night 1: 30 tests, q = 0.0003 × 30 = 0.009 ≤ 0.01: it opens
    one = detect.run(_night(monkeypatch, "0.0003", 29), keep_q=KEEP_Q)
    assert _step(one.results).passed
    return findings.build(one.results, [], D1, keep=KEEP)


def test_more_tests_with_the_same_p_keep_an_open_finding_open(monkeypatch):
    first = _opened(monkeypatch)
    (f,) = [x for x in first if x["metric"] == "nashik_rework"]
    assert f["change"] == "New"
    # night 2: breakdowns added, 80 tests, the same p: q = 0.024, over 0.01 but under 0.05
    two = detect.run(_night(monkeypatch, "0.0003", 79), open_before=_open(first), keep_q=KEEP_Q)
    r = _step(two.results)
    assert Decimal(r.q_value) == Decimal("0.024")
    assert r.passed and r.reason is None
    assert r.params["kept_open"] == "true" and r.params["keep_q"] == "0.05"
    assert r.params["fdr_q"] == "0.01" and r.params["tests"] == 80
    second = {x["fingerprint"]: x for x in findings.build(two.results, first, D2, keep=KEEP)}
    kept = second[f["fingerprint"]]
    assert kept["status"] == "open" and kept["change"] == "Open · day 2"
    assert kept["first_seen"] == f["first_seen"]
    assert two.counts()["findings"] == 1


def test_a_new_finding_still_needs_the_opening_bar(monkeypatch):
    got = detect.run(_night(monkeypatch, "0.0003", 79), keep_q=KEEP_Q)
    r = _step(got.results)
    assert not r.passed and r.reason.startswith("not significant after FDR")
    assert "kept_open" not in r.params


def test_a_p_that_weakens_past_the_keep_level_closes_it_and_says_so(monkeypatch):
    first = _opened(monkeypatch)
    # p weakens to 0.0008 over 80 tests: q = 0.064 > 0.05
    two = detect.run(_night(monkeypatch, "0.0008", 79), open_before=_open(first), keep_q=KEEP_Q)
    assert not _step(two.results).passed
    (gone,) = [
        x for x in findings.build(two.results, first, D2, keep=KEEP) if x["status"] == "resolved"
    ]
    assert gone["resolved"]["reason"] == findings.WEAKER
    assert "Cleared by data" not in gone["resolved"]["reason"]


def test_many_more_tests_with_the_same_p_close_it_as_below_the_bar(monkeypatch):
    first = _opened(monkeypatch)
    # the same p over 200 tests: q = 0.06 > 0.05; the data didn't change, the bar did
    two = detect.run(_night(monkeypatch, "0.0003", 199), open_before=_open(first), keep_q=KEEP_Q)
    assert not _step(two.results).passed
    keep = {**KEEP, "tests_before": 30}
    (gone,) = [
        x for x in findings.build(two.results, first, D2, keep=keep) if x["status"] == "resolved"
    ]
    assert gone["resolved"]["reason"] == findings.MORE_CHECKS
    # without last night's count it still never says "Cleared by data"
    (gone,) = [
        x for x in findings.build(two.results, first, D2, keep=KEEP) if x["status"] == "resolved"
    ]
    assert gone["resolved"]["reason"] == findings.HELD_BELOW


def test_a_signal_that_is_gone_is_still_cleared_by_data(monkeypatch):
    first = _opened(monkeypatch)
    two = detect.run(_night(monkeypatch, "0.3", 29), open_before=_open(first), keep_q=KEEP_Q)
    (gone,) = [
        x for x in findings.build(two.results, first, D2, keep=KEEP) if x["status"] == "resolved"
    ]
    assert gone["resolved"]["reason"] == "Cleared by data"


def test_a_run_without_the_rule_closes_as_before(monkeypatch):
    """A run sealed before #142 has no `fdr_keep` in its context: replay must give its bytes."""
    one = detect.run(_night(monkeypatch, "0.0003", 29))
    first = findings.build(one.results, [], D1)
    two = detect.run(_night(monkeypatch, "0.0003", 79), open_before=_open(first))
    r = _step(two.results)
    assert not r.passed and "keep_q" not in r.params and "kept_open" not in r.params
    (gone,) = [x for x in findings.build(two.results, first, D2) if x["status"] == "resolved"]
    want = f"Cleared by data · not significant after FDR (q {r.q_value})"
    assert gone["resolved"]["reason"] == want


def test_the_context_carries_the_rule_and_last_nights_test_count():
    ctx = carry_context([], [], [], None, tests_before=412)
    assert ctx["fdr_keep"] == {"q": "0.05", "tests_before": 412}
    assert carry_context([], [], [])["fdr_keep"] == {"q": "0.05"}


def test_provenance_shows_both_bars_and_the_nights_test_count():
    from othor.web.sweep_view import gates

    doc = spec(nashik_rework={"name": "Nashik rework", "unit": "inr"})
    rv = SimpleNamespace(compiled=doc)
    f = {"detector": "changepoint", "p_value": "0.0003", "q_value": "0.024", "money": None}
    result = {"params": {"fdr_q": "0.01", "keep_q": "0.05", "tests": 80, "kept_open": "true"}}
    chance = next(g for g in gates(rv, f, result) if g["name"] == "It isn’t chance")  # type: ignore[arg-type]
    assert chance["why"] == (
        "After correcting all 80 tests of the night · needs under 0.01 to open, "
        "under 0.05 to stay open"
    )
    # a run before the rule reads as it did
    old = {"params": {"fdr_q": "0.01", "tests": 30}}
    chance = next(g for g in gates(rv, f, old) if g["name"] == "It isn’t chance")  # type: ignore[arg-type]
    assert chance["why"] == "After correcting all 30 tests of the night · needs under 0.01"


def test_the_closing_reasons_read_plainly_everywhere():
    from othor.briefs.resolved import plain_reason
    from othor.briefs.since_week import how

    for reason in (findings.MORE_CHECKS, findings.HELD_BELOW, findings.WEAKER):
        assert "Cleared" not in plain_reason("resolved", reason)
        assert "numbers no longer show it" not in plain_reason("resolved", reason)
        assert "Back to normal" not in how("resolved", reason, "2026-11-02", "changepoint")
    # a night sealed before #142 stored the old words: shown plainly, without "FDR"
    old = "Cleared by data · not significant after FDR (q 0.0108)"
    assert findings.shown_reason(old) == findings.BELOW_BAR
    assert plain_reason("resolved", old) == findings.BELOW_BAR
    assert findings.shown_reason("Cleared by data") == "Cleared by data"
