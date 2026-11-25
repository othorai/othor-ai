# SPDX-License-Identifier: Apache-2.0
"""0.35: one way to write and read who did something."""

import uuid

from othor.core import actors

UID = "6967d83f-d237-4407-9654-4d8cc10b056e"
NAMES = {UID: "Uday Kamath"}


def test_people_in_every_form_read_as_that_person() -> None:
    for raw in (UID, f"user:{UID}", uuid.UUID(UID)):
        a = actors.parse(raw)
        assert a.kind == actors.USER and a.user_id == UID
        assert actors.label(a, NAMES) == "Uday Kamath"
    assert actors.user(uuid.UUID(UID)) == UID


def test_an_old_email_actor_reads_as_its_person() -> None:
    a = actors.parse("UK@indigothread.example")
    assert a.kind == actors.USER and a.email == "uk@indigothread.example"
    by_email = {"uk@indigothread.example": UID}
    assert actors.label(a, NAMES, by_email=by_email) == "Uday Kamath"
    assert actors.person_id(a, by_email) == UID
    assert actors.label(a, {}) == "uk@indigothread.example"  # no longer a user: the address


def test_othor_ai_on_a_persons_request() -> None:
    raw = actors.othor_ai(UID)
    assert raw == f"othor_ai:{UID}" and len(raw) <= 64
    a = actors.parse(raw)
    assert a.kind == actors.OTHOR_AI and a.user_id == UID
    assert actors.label(a, NAMES) == "Othor AI (asked by Uday)"
    assert actors.othor_ai(f"user:{UID}") == raw
    assert actors.othor_ai(None) == "othor_ai" and actors.othor_ai("owner") == "othor_ai"
    assert actors.label(actors.parse("othor_ai"), NAMES) == "Othor AI"


def test_system_support_and_apps() -> None:
    assert actors.label(actors.parse("system"), {}) == "System"
    assert actors.label(actors.parse("trial"), {}) == "System"
    assert actors.label(actors.parse("operator"), {}) == "Othor support"
    assert actors.label(actors.parse("cli"), {}) == "Othor support"
    assert actors.label(actors.parse("sud demo"), {}) == "Othor support"
    m = actors.parse(actors.mcp("k1"))
    assert m.kind == actors.MCP and m.key == "k1"
    assert actors.label(m, {}) == "A connected AI app"
    assert actors.label(actors.parse("recipient:abc"), {}) == "Someone the brief is sent to"


def test_a_former_member() -> None:
    assert actors.label(actors.parse(str(uuid.uuid4())), NAMES) == "A former member"
