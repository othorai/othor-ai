# SPDX-License-Identifier: Apache-2.0
"""Home questions from rules (M10a, docs/04 §10): unconfirmed known patterns, detectors that
could not run for want of a record, and the order Home asks them in."""

from __future__ import annotations

from othor.engine.asks import ask, rank_key
from othor.ontology.questions import needs_candidates, pattern_candidates

SURAT = {
    "key": "surat_shutdown",
    "name": "Surat plant shutdown",
    "short": "the Surat shutdown",
    "window": {"kind": "annual", "start": "08-10", "end": "08-24"},
    "confirmed": False,
}


def test_an_unconfirmed_pattern_asks_same_dates_every_year():
    spec = {"suppressions": [SURAT, {**SURAT, "key": "diwali", "confirmed": True}]}
    (q,) = pattern_candidates(spec)
    assert q["text"] == "Is the Surat shutdown 10–24 Aug the same every year?"
    assert q["kind"] == "pattern" and q["subject"] == {"pattern": "surat_shutdown"}
    assert q["quick_replies"] == ["Yes, every year", "No, the dates change"]
    assert pattern_candidates({"suppressions": [{**SURAT, "entity": "Skarfab"}]}) == []


def test_a_detector_needing_a_record_asks_for_it_once_per_record():
    cov = [
        {"detector": "pair_break", "status": "could_not_run", "needs": "pair"},
        {"detector": "lead_lag", "status": "could_not_run", "needs": "pair"},
        {"detector": "forecast_breach", "status": "could_not_run", "needs": "forecast"},
        {"detector": "changepoint", "status": "ran", "needs": None},
    ]
    (q,) = needs_candidates(cov)
    # v0.12: the owner's terms, and one plain line on what the check does; no count of Sweep
    # tests on the question (#33), the count stays in `unblocks` for the checks strip
    assert q["text"] == "Should these two numbers move together?"
    assert q["why"].startswith("Sweep watches pairs that should move together")
    assert "Sweep test" not in q["why"] and not any(c.isdigit() for c in q["why"])
    assert q["unblocks"]["detectors"] == ["lead_lag", "pair_break"]
    assert q["subject"] == {"needs": "pair", "detectors": ["lead_lag", "pair_break"]}
    assert needs_candidates(cov) == needs_candidates(list(reversed(cov)))


def test_finding_questions_carry_kind_subject_and_replies():
    f = {
        "status": "open",
        "state": "not_determinable",
        "detector": "missing_records",
        "fingerprint": "fpN",
        "metric": "invoiced_value",
        "entities": ["Skarfab"],
        "window": ["2026-10-09", "2026-10-31"],
        "money": "1200000.00",
        "slots": {"name": "Skarfab", "gap_days": 23, "median_days": 7, "last": "2026-10-09"},
        "reasons": [{"text": "a pause", "ruled_out": False}],
    }
    q = ask({"owners": []}, f)
    assert q["kind"] == "not_determinable" and q["quick_replies"][0] == "Yes, expected"
    assert q["subject"]["entity"] == "Skarfab" and q["subject"]["since"] == "2026-10-09"
    nc = {
        **f,
        "state": "needs_confirmation",
        "detector": "cross_source",
        "slots": {"count": 37, "left_source": "sales_dw", "right_source": "erp_db"},
    }
    q2 = ask({"owners": []}, nc)
    assert q2["subject"]["sides"] == ["sales_dw", "erp_db"]
    assert q2["quick_replies"] == ["sales_dw is right", "erp_db is right", "Not sure, look into it"]
    assert q2["rank"] < q["rank"]


def test_the_order_is_kind_then_the_larger_amount():
    keys = [
        rank_key("pattern"),
        rank_key("not_determinable", "100"),
        rank_key("needs_confirmation", "5"),
        rank_key("not_determinable", "9000000"),
        rank_key("unmapped_plant"),
    ]
    assert sorted(keys) == [keys[2], keys[3], keys[1], keys[0], keys[4]]
