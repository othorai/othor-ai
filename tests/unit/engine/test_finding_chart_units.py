# SPDX-License-Identifier: Apache-2.0
"""indigo2, 12 Nov 2026: an order-value spread chart read "average 56,114.1 d" (the spread chart
was built for dispatch time and always said days) and a step chart read 0.02–0.04 Cr for about
₹50,000. Charts take the metric's own unit, money in crore, lakh or rupees by size."""

from __future__ import annotations

from othor.engine import finding_charts as fc

SPEC = {
    "metrics": {
        "aov": {"name": "Average order value", "unit": "inr"},
        "revenue": {"name": "Net revenue", "unit": "inr"},
        "dispatch": {"name": "Dispatch time", "unit": "days"},
    }
}


def test_money_is_scaled_by_its_size():
    assert fc._scaled(SPEC, "revenue", [1.25e7, 1.3e7]) == (fc.CR, 1e7)
    unit, div = fc._scaled(SPEC, "aov", [5.6e5, 6.1e5])
    assert (unit.scale, div) == ("l", 1e5)
    unit, div = fc._scaled(SPEC, "aov", [56114.0, 60000.0])
    assert (unit.type, unit.scale, div) == ("currency", "none", 1.0)


def test_labels_use_the_metrics_unit():
    assert fc._label(fc._unit(SPEC, "aov"), 56114.1) == "₹56,114.1"
    assert fc._label(fc._unit(SPEC, "dispatch"), 4.3) == "4.3 d"
    unit, div = fc._scaled(SPEC, "dispatch", [4.0, 9.0])
    assert unit.type == "days" and div == 1.0
