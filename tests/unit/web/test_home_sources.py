# SPDX-License-Identifier: Apache-2.0
"""Home's Data sources table (#57, owner 14 Nov 2026): each source and its update status, read
from the run's stored weather facts (computed by code, never recomputed on the page)."""

from __future__ import annotations

from othor.web.home_view import source_status

FACTS = {
    "unread": ["erp_db"],
    "sources_fresh": {
        "erp_db": {"newest": "2026-11-12", "updated": True, "late": False},
        "Sales sheet": {"newest": "2026-11-11", "updated": False, "late": True},
        "Weekly sheet": {"newest": "2026-11-09", "updated": False, "late": False},
        "Targets": {"newest": None, "updated": None, "late": False},
        "sales_dw": {"newest": "2026-11-12", "updated": True, "late": False},
    },
}


def test_each_source_says_whether_it_was_updated_yesterday() -> None:
    assert source_status("sales_dw", FACTS) == ("Updated", "ok", "12 Nov")
    assert source_status("Sales sheet", FACTS) == ("Not updated since 11 Nov", "warn", "11 Nov")
    # a source that only gets rows on its own days: the same words, without the warning
    assert source_status("Weekly sheet", FACTS) == ("Not updated since 9 Nov", "warn", "9 Nov")
    assert source_status("Targets", FACTS) == ("No daily numbers", "muted", "–")


def test_a_source_that_couldnt_be_read_says_so_first() -> None:
    assert source_status("erp_db", FACTS) == ("Couldn’t be read", "neg", "12 Nov")


def test_a_source_no_metric_reads_yet() -> None:
    assert source_status("New sheet", FACTS) == ("Not used yet", "muted", "–")
    assert source_status("x", None) == ("Not read yet", "muted", "–")
