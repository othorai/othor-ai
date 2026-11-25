# SPDX-License-Identifier: Apache-2.0
"""0.35: every action has a kind and a plain sentence."""

from othor.audit import words


def test_every_named_action_has_a_known_kind() -> None:
    kinds = set(words.KIND_LABEL)
    assert all(k in kinds for k, _ in words.WORDS.values())
    assert all(k in kinds for k, _ in words.FAMILIES.values())
    assert all(k in kinds for k in words.PREFIX_KIND.values())


def test_sentences() -> None:
    assert words.sentence("auth.signed_in") == "Signed in"
    assert words.sentence("source.created", after={"kind": "sheets"}) == "Added Google Sheets"
    assert words.sentence("run.finished", after={"number": 61}) == "Nightly run {obj} finished"
    assert words.sentence("run.finished", after={"kind": "manual"}) == "Run {obj} finished"
    assert "sealed" not in words.sentence("run.finished", after={"number": 61})
    assert (
        words.sentence("member.invited", after={"email": "p@x.example", "role": "admin"})
        == "Invited p@x.example as admin"
    )
    assert words.sentence("brief.delivered", after={"recipient": "r", "status": "sent"}) == (
        "Sent the {obj} to one person"
    )


def test_an_applied_draft_says_so() -> None:
    s = words.sentence("othor_chat.applied", after={"changes": 2, "drafted_by": "othor_ai"})
    assert s == "Applied Othor AI's draft: 2 changes"
    s = words.sentence("question.answered", after={"drafted_by": "othor_ai"})
    assert s == "Applied Othor AI's draft: answered a question"


def test_unknown_actions_read_plainly_and_families_are_named() -> None:
    assert words.sentence("widget.re_tuned") == "Widget · re tuned"
    assert words.kind_of("widget.re_tuned") == "settings"
    assert words.kind_of("source.something_new") == "sources"
    assert words.named("setup.chat_needs_source") and words.named("mail.run_failed")
    assert words.kind_of("mail.export_ready") == "data"
    assert words.kind_of("mail.something") == "briefs"
