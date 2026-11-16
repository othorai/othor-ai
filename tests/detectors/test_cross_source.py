# SPDX-License-Identifier: Apache-2.0
"""Cross-source contradiction: unmatched keys and periods that disagree beyond tolerance and the
floor are Needs confirmation with the difference as money; matching sources don't fire."""

from othor.engine.detectors.cross_source import run
from tests.detectors.synth import context, spec


def test_unmatched_dispatches_need_confirmation():
    unmatched = [[f"SO-{24100 + i}", "2026-09-20", 513_513.51, "Ravira Exports"] for i in range(37)]
    probe = {
        "reconcile": [
            {
                "key": "dispatch_invoice",
                "kind": "keys",
                "lag_days": 30,
                "left_label": "orders dispatched in sales_dw",
                "right_label": "invoices in erp_db",
                "unmatched": unmatched,
                "left_count": 4200,
            }
        ]
    }
    (r,), cov = run(context(spec(), [], probe))
    assert r.state_hint == "needs_confirmation" and r.slots["count"] == 37
    assert abs(float(r.money) - 37 * 513_513.51) < 0.05 and len(r.reasons) == 3
    assert r.evidence[0]["key"].startswith("SO-") and cov.status == "ran"


def test_periods_that_disagree_and_that_agree():
    rows = [["2026-09-01", "48100000", "48100000"], ["2026-10-01", "46200000", "45400000"]]
    probe = {
        "reconcile": [
            {
                "key": "mis_erp",
                "kind": "periods",
                "a_label": "erp_db",
                "b_label": "MIS",
                "rows": rows,
            }
        ]
    }
    (r,), _ = run(context(spec(), [], probe))
    assert r.slots["period"] == "2026-10-01" and abs(float(r.money) - 800_000) < 0.01
    rows[1][2] = "46190000"  # within 0.5% and under the floor
    (r,), _ = run(context(spec(), [], probe))
    assert float(r.p_value) == 1 and r.reason == "the two sources agree within tolerance"
    out, cov = run(context(spec(), [], {}))
    assert out == [] and cov.needs == "reconcile"


def test_the_floor_applies_to_the_total_change_for_the_period():
    """P16 (5 Nov 2026): two late credit notes of ₹2.9 L in one week are one ₹5.8 L difference
    against the MIS, over the ₹5 L floor; one note of ₹2.9 L alone is under it."""
    from decimal import Decimal

    from othor.engine.detect import run as detect_run

    week = 15_000_000
    notes = [290_000, 290_000]

    def probe(late):
        erp = week - sum(late)  # erp_db is short of the credit notes not yet posted
        return {
            "reconcile": [
                {
                    "key": "mis_erp",
                    "kind": "periods",
                    "a_label": "erp_db",
                    "b_label": "MIS",
                    "rows": [
                        ["2026-10-12", str(week), str(week)],
                        ["2026-10-19", str(erp), str(week)],
                    ],
                }
            ]
        }

    (two,), _ = run(context(spec(), [], probe(notes)))
    assert Decimal(two.money) == Decimal("580000.00")
    det = detect_run(context(spec(), [], probe(notes)))
    assert [r.passed for r in det.results if r.detector == "cross_source"] == [True]
    det = detect_run(context(spec(), [], probe(notes[:1])))
    (one,) = [r for r in det.results if r.detector == "cross_source"]
    assert not one.passed  # under the floor: the sources agree within tolerance
