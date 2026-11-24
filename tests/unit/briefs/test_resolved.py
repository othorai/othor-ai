# SPDX-License-Identifier: Apache-2.0
"""Recently resolved, the Action plan's history (owner, 22 Nov 2026): an action counts as resolved
only when its reason is gone (the finding resolved, covered or merged; the goal back on track; the
watch inside its limit or stopped; or the owner held it), never when it merely dropped out of
today's three."""

from __future__ import annotations

import datetime as dt

from othor.briefs.resolved import FindingNow, Hold, Seen, plain_reason, resolve

D = dt.date
TODAY = D(2026, 11, 22)
DAYS = [D(2026, 11, d) for d in range(1, 23)]  # an issue every day


def _seen(
    key: str,
    kind: str = "finding",
    fps: tuple[str, ...] = (),
    first: int = 9,
    last: int = 20,
    goal: str | None = None,
    watch: str | None = None,
) -> Seen:
    return Seen(
        key=key,
        kind=kind,
        fingerprints=fps,
        said=f"Do {key}",
        first=D(2026, 11, first),
        last=D(2026, 11, last),
        briefs=("Leadership",),
        goal=goal,
        watch=watch,
    )


def _run(seen: list[Seen], **kw: object) -> list:
    args: dict = dict(
        today_keys=set(),
        today_fps=set(),
        findings={},
        holds={},
        goals={},
        watches={},
        days=DAYS,
        today=TODAY,
    )
    args.update(kw)
    return resolve(seen, **args)  # type: ignore[arg-type]


def test_a_resolved_finding_is_listed_with_its_day_and_reason() -> None:
    got = _run(
        [_seen("finding:a", fps=("a",))],
        findings={"a": FindingNow("resolved", D(2026, 11, 21), "Cleared by data", 12)},
    )
    assert len(got) == 1
    r = got[0]
    assert (r.on, r.reason, r.icon, r.fingerprint, r.run) == (
        D(2026, 11, 21),
        "The numbers no longer show it",
        "check",
        "a",
        12,
    )
    assert r.first == D(2026, 11, 9)


def test_dropped_but_still_open_is_not_listed() -> None:
    got = _run(
        [_seen("finding:a", fps=("a",))],
        findings={"a": FindingNow("open", None, "", 12)},
    )
    assert got == []


def test_an_action_still_in_today_s_plan_is_not_listed() -> None:
    s = _seen("finding:a", fps=("a",))
    res = {"a": FindingNow("resolved", D(2026, 11, 21), "Cleared by data", 12)}
    assert _run([s], findings=res, today_keys={"finding:a"}) == []
    # folded into another of today's actions: its finding is still in the plan
    assert _run([s], findings=res, today_fps={"a"}) == []


def test_a_goal_back_on_track_is_listed_on_the_day_its_action_left() -> None:
    got = _run([_seen("goal:g", kind="goal", goal="g", last=18)], goals={"g": ("ok", True)})
    assert [(r.reason, r.on, r.icon) for r in got] == [("Back on track", D(2026, 11, 19), "check")]
    # still off track: not resolved
    assert _run([_seen("goal:g", kind="goal", goal="g")], goals={"g": ("off", False)}) == []


def test_muted_and_snoozed_say_so_with_their_day() -> None:
    got = _run(
        [_seen("finding:a", fps=("a",)), _seen("finding:b", fps=("b",))],
        findings={"a": FindingNow("open", None, "", 12), "b": FindingNow("open", None, "", 12)},
        holds={
            "a": Hold("muted", D(2026, 11, 21), None),
            "b": Hold("snoozed", D(2026, 11, 20), D(2026, 11, 27)),
        },
    )
    by = {r.key: r for r in got}
    assert (by["finding:a"].reason, by["finding:a"].icon) == ("You muted it", "eye-off")
    assert by["finding:a"].on == D(2026, 11, 21)
    assert (by["finding:b"].reason, by["finding:b"].icon) == (
        "You snoozed it until 27 Nov",
        "clock",
    )


def test_a_folded_action_resolves_when_its_last_finding_does() -> None:
    s = _seen("finding:a", fps=("a", "b"))
    one_open = _run(
        [s],
        findings={
            "a": FindingNow("resolved", D(2026, 11, 15), "Cleared by data", 10),
            "b": FindingNow("open", None, "", 12),
        },
    )
    assert one_open == []
    got = _run(
        [s],
        findings={
            "a": FindingNow("resolved", D(2026, 11, 15), "Cleared by data", 10),
            "b": FindingNow("covered", D(2026, 11, 19), "", 12),
        },
    )
    assert len(got) == 1
    assert got[0].on == D(2026, 11, 19)  # the later of the two
    assert got[0].reason == (
        "The numbers no longer show it; matches a known pattern in your Business Model"
    )


def test_a_watch_back_inside_or_stopped() -> None:
    w = _seen("watch:w", kind="watch", watch="w", last=19)
    inside = _run([w], watches={"w": ("within", None)})
    assert [(r.reason, r.on) for r in inside] == [("Back inside its limit", D(2026, 11, 20))]
    stopped = _run([w], watches={"w": ("breached", D(2026, 11, 21))})
    assert [(r.reason, r.on) for r in stopped] == [("You stopped watching it", D(2026, 11, 21))]
    assert _run([w], watches={"w": ("breached", None)}) == []


def test_only_the_last_30_days_newest_first() -> None:
    old = _seen("finding:old", fps=("old",), first=1, last=2)
    a = _seen("finding:a", fps=("a",))
    b = _seen("finding:b", fps=("b",))
    got = _run(
        [old, a, b],
        findings={
            "old": FindingNow("resolved", D(2026, 10, 20), "Cleared by data", 3),
            "a": FindingNow("resolved", D(2026, 11, 18), "Cleared by data", 9),
            "b": FindingNow("resolved", D(2026, 11, 21), "No longer tested", 12),
        },
    )
    assert [r.key for r in got] == ["finding:b", "finding:a"]
    assert got[0].reason == "No longer checked"


def test_plain_reasons() -> None:
    assert (
        plain_reason("resolved", "Cleared by data · no signal") == "The numbers no longer show it"
    )
    assert plain_reason("resolved", "Explained by your answer (30 Oct)") == (
        "Explained by your answer (30 Oct)"
    )
    assert plain_reason("resolved", "Below the materiality floor") == (
        "Now below the amount worth reporting"
    )
    assert plain_reason("merged", "") == "Now part of another finding"
