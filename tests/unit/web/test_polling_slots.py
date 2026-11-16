# SPDX-License-Identifier: Apache-2.0
"""Polling regions keep their height while they fill in (M12): a 1 s poll never moves the page.
The drafting list always shows the same number of rows (drafted rows plus skeletons), and the
first run holds four skeleton lines until the headline numbers arrive."""

from __future__ import annotations

from types import SimpleNamespace as NS

from othor.web.templating import templates


def _progress(done: int, total: int) -> str:
    items = [NS(name=f"Metric {i}", kind="done", check=False, note="") for i in range(done)]
    return templates.env.get_template("setup/_progress.html").render(
        items=items, done=done, total=total
    )


def test_the_drafting_list_keeps_its_rows() -> None:
    counts = {_progress(d, 6).count('class="progress-row') for d in range(7)}
    assert counts == {6}
    assert {_progress(d, 20).count('class="progress-row') for d in (0, 5, 8, 12)} == {8}
    assert _progress(0, 0).count('class="progress-row') == 3  # reading, total not known yet


def _first_run(columns: list, finished: bool = False) -> str:
    from othor.web.run_view import FirstRun

    view = FirstRun(state="done" if finished else "running", columns=columns)
    return templates.env.get_template("setup/_first_run.html").render(
        view=view, csrf_token="t", features=NS(BRIEFS=True)
    )


def test_the_first_run_holds_every_stage_place() -> None:
    """M11c (2o): the five stages are on the card from the start, waiting ones muted, so the
    page doesn't jump as the run fills in."""
    from othor.web.run_view import Column

    starting = _first_run([Column("Queries", "muted", "waiting")])
    assert starting.count('class="scan-step run-step') == 5
    done = _first_run([Column("Queries", "ok", "43 of 43 · 0.3 s")], finished=True)
    assert done.count('class="scan-step run-step') == 5
