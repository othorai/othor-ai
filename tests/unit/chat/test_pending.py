# SPDX-License-Identifier: Apache-2.0
"""The pending set (Chat 1.0): a reply's edits keep only real items and their own options; Apply
counts only picked or changed items with an answer; skipped and needs-you items never apply."""

from __future__ import annotations

from typing import Any

from othor.chat import pending


def _items() -> list[dict[str, Any]]:
    return [
        {
            "id": "q:1",
            "status": "pick",
            "answer_type": "choice",
            "choice": "a",
            "options": [{"key": "a"}, {"key": "b"}],
        },
        {
            "id": "q:2",
            "status": "needs_you",
            "answer_type": "choice",
            "choice": None,
            "options": [{"key": "x"}, {"key": "y"}],
        },
        {
            "id": "q:3",
            "status": "needs_you",
            "answer_type": "free_text",
            "answer": None,
            "options": [],
        },
    ]


def test_only_picked_or_changed_with_an_answer_apply() -> None:
    items = _items()
    assert [i["id"] for i in pending.to_apply(items)] == ["q:1"]
    assert pending.counts(items) == {"total": 3, "picked": 1, "needs_you": 2, "apply": 1}


def test_a_reply_edits_only_real_items_and_their_own_options() -> None:
    items = _items()
    edits = [
        {"id": "q:2", "action": "pick", "option": "y"},  # valid
        {"id": "q:2", "action": "pick", "option": "z"},  # not one of its options
        {"id": "q:9", "action": "pick", "option": "a"},  # no such item
        {"id": "q:3", "action": "answer", "text": "Paused till March"},  # free text
        {"id": "q:1", "action": "answer", "text": "nope"},  # not a free-text item
        {"id": "q:1", "action": "skip"},
        {"id": "q:1", "action": "delete"},  # unknown action
        "garbage",
    ]
    out, dropped = pending.edit(items, edits)
    assert dropped == 5
    by = {i["id"]: i for i in out}
    assert by["q:2"]["choice"] == "y" and by["q:2"]["status"] == "changed"
    assert by["q:3"]["answer"] == "Paused till March" and by["q:3"]["status"] == "changed"
    assert by["q:1"]["status"] == "skipped"
    assert [i["id"] for i in pending.to_apply(out)] == ["q:2", "q:3"]
    assert items[1]["choice"] is None  # the original set is untouched


def test_edits_that_are_not_a_list_change_nothing() -> None:
    out, dropped = pending.edit(_items(), {"id": "q:2"})
    assert out == _items() and dropped == 0
