# SPDX-License-Identifier: Apache-2.0
"""10c: a model's tab fix is typed and checked; a column or unit it doesn't know is dropped."""

from __future__ import annotations

from types import SimpleNamespace

from othor.setup import tab_fix


def _tab():
    return SimpleNamespace(
        tab_name="Open orders",
        shape={
            "header_row": 3,
            "last_row": 88,
            "grid_rows": 89,
            "columns": [
                {"header": "Order date", "kind": "date", "unit": None},
                {"header": "Value (₹ L)", "kind": "number", "unit": "lakh"},
            ],
        },
    )


def test_units_fix_is_checked():
    out = {
        "patch": {
            "header_row": 0,
            "last_row": 0,
            "units": [{"column": "Value (₹ L)", "unit": "crore"}],
        }
    }
    got = tab_fix.proposal(out, _tab())
    assert got == {
        "shape": {"header_row": 3, "unit_col": "Value (₹ L)", "unit": "crore"},
        "lines": ["Value (₹ L): read as ₹ crore"],
    }


def test_unknown_column_unit_or_row_is_dropped():
    for patch in (
        {"header_row": 0, "last_row": 0, "units": [{"column": "Customer", "unit": "crore"}]},
        {"header_row": 0, "last_row": 0, "units": [{"column": "Value (₹ L)", "unit": "gallons"}]},
        {"header_row": 400, "last_row": 0, "units": []},
    ):
        assert tab_fix.proposal({"patch": patch}, _tab()) is None


def test_inputs_carry_no_cells():
    inputs = tab_fix.model_inputs(_tab(), [])  # type: ignore[arg-type]
    assert "preview" not in str(inputs) and "rows" not in inputs


def test_a_row_number_nobody_gave_is_dropped():
    out = {"patch": {"header_row": 7, "last_row": 0, "units": []}}
    inputs = tab_fix.model_inputs(_tab(), [])  # type: ignore[arg-type]
    assert tab_fix.proposal(out, _tab(), inputs) is None
