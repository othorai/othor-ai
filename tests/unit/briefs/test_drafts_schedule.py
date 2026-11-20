# SPDX-License-Identifier: Apache-2.0
"""Tell Othor AI and New brief can set Weekly or Monthly (M15.7c): the model's day of the month
is kept only when the owner typed that number (no number comes from a model)."""

from __future__ import annotations

from othor.briefs.drafts import DRAFT, EDIT, _on


def test_the_prompts_are_the_versions_that_know_weekly_and_monthly() -> None:
    assert DRAFT == ("brief_draft", "v4") and EDIT == ("brief_edit", "v3")


def test_a_weekday_is_kept_as_drafted() -> None:
    assert _on({"on": "Mon"}, "send it every monday") == "mon"


def test_a_day_of_the_month_needs_the_owners_own_number() -> None:
    assert _on({"on": "15"}, "monthly, on the 15th") == "15"
    assert _on({"on": "15"}, "make it monthly") is None
    assert _on({}, "anything") is None
