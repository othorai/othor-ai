# SPDX-License-Identifier: Apache-2.0
"""#31: "Drafting your metrics" never looks frozen: a live line names what is being read, then the
drafting, then the count, with how long it has run. Names and counts only."""

from __future__ import annotations

from othor.web.setup_view import elapsed_text, stage_line

NAMES = ["MIS-Weekly.xlsx", "erp_db", "sales_dw"]


def test_while_drafting_it_names_each_input_in_turn_then_the_drafting():
    lines = [stage_line("drafting", NAMES, 0, 0, s) for s in (0, 2, 4, 6, 30)]
    assert lines == [
        "Reading MIS-Weekly.xlsx…",
        "Reading erp_db…",
        "Reading sales_dw…",
        "Othor AI is drafting your metrics…",
        "Othor AI is drafting your metrics…",
    ]


def test_while_computing_it_counts():
    assert (
        stage_line("computing", NAMES, 6, 20, 40) == "Computing each metric on your data · 6 of 20"
    )
    assert stage_line("drafting", [], 0, 0, 0) == "Othor AI is drafting your metrics…"


def test_the_elapsed_time_reads_naturally():
    assert [elapsed_text(s) for s in (0, 2, 3, 59, 65)] == ["", "", "3 s", "59 s", "1 min 05 s"]
