# SPDX-License-Identifier: Apache-2.0
"""The brief PDF's subset fonts carry the issue's date (14 Nov 2026): fontTools stamps a subset
font with SOURCE_DATE_EPOCH, else the clock, so exports a second apart differed on Linux."""

from __future__ import annotations

import datetime as dt
import os

import fontTools.misc.timeTools as tt

from othor.web.brief_pdf import fonts_dated


def test_fonts_are_stamped_with_the_issue_date_and_the_variable_is_restored() -> None:
    before = os.environ.get("SOURCE_DATE_EPOCH")
    want = int(dt.datetime(2026, 11, 1, tzinfo=dt.UTC).timestamp()) - tt.epoch_diff
    with fonts_dated(dt.date(2026, 11, 1)):
        a = tt.timestampNow()
        b = tt.timestampNow()
    assert a == b == want
    assert os.environ.get("SOURCE_DATE_EPOCH") == before
