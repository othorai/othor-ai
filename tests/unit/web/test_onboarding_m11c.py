# SPDX-License-Identifier: Apache-2.0
"""Onboarding steps 1–3 in the new layout (M11c · C): the step bar in the column, and the Business
message box in each of its states."""

from __future__ import annotations

from othor.web.templating import templates

STEPS = '{% from "components/blocks.html" import column_steps %}{{ column_steps(s, n) }}'
NAMES = ["Company", "Upload", "Business", "Sources", "Metrics", "First run"]


def test_the_step_bar_says_where_you_are_and_what_is_next() -> None:
    html = templates.env.from_string(STEPS).render(s=NAMES, n=3)
    assert "Step 3 of 6" in html and "· Business" in html and "Next: Sources" in html
    assert html.count('class="seg done"') == 2 and html.count('class="seg now"') == 1
    assert 'aria-current="step"' in html
    last = templates.env.from_string(STEPS).render(s=NAMES, n=6)
    assert "Next:" not in last  # the last step has no next


def _box(**kw: object) -> str:
    ctx = {
        "box": "nofiles",
        "text": "",
        "uploads": [],
        "drafted_from": [],
        "example": "We make snacks.",
        "not_covered": None,
        "oob": False,
    }
    ctx.update(kw)
    return templates.env.get_template("setup/_business_box.html").render(**ctx)


def test_waiting_shows_the_files_and_polls_until_the_draft_lands() -> None:
    html = _box(
        box="waiting",
        uploads=[{"name": "MIS.xlsx", "reading": True}, {"name": "r.sql", "reading": False}],
        oob=True,
    )
    assert 'hx-get="/setup/business/draft"' in html and 'hx-trigger="every 2s"' in html
    assert "MIS.xlsx · being read" in html and "r.sql · read" in html
    assert "<textarea" not in html  # nothing to type into while it drafts
    assert "Continue opens when the draft is ready" in html and "disabled" in html  # oob footer


def test_waiting_shows_how_long_it_has_been_drafting() -> None:
    """Owner, 20 Nov 2026: a timer so the box never looks stuck; a calm note past 90 seconds."""
    html = _box(box="waiting", uploads=[{"name": "MIS.xlsx", "reading": False}], elapsed="0:42")
    assert "Drafting your message. You can edit it when it’s ready." in html and "· 0:42" in html
    assert "Still working" not in html
    slow = _box(box="waiting", uploads=[], elapsed="1:35", slow=True)
    assert "· 1:35" in slow and "Still working: several files take a little longer." in slow
    assert "· " not in _box(box="waiting", uploads=[]).split("Drafting your message")[1][:60]


def test_a_drafted_box_is_filled_and_carries_the_draft_note() -> None:
    html = _box(
        box="drafted", text="We make technical textiles.", drafted_from=["MIS.xlsx", "r.sql"]
    )
    assert "Drafted from MIS.xlsx and r.sql · edit or delete anything" in html
    assert ">We make technical textiles.</textarea>" in html
    assert (
        "Drafts only. Numbers are computed, not written. Nothing changes until you apply." in html
    )
    assert "hx-get" not in html  # done: no more polling
    assert "Clear" not in html


def test_no_files_shows_the_example_as_placeholder_and_no_draft_note() -> None:
    html = _box(box="nofiles")
    assert "No files to draft from. Write a few lines in your own words." in html
    assert 'placeholder="For example:' in html and "We make snacks." in html
    assert "Drafts only." not in html
