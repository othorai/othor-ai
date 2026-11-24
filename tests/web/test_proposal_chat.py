# SPDX-License-Identifier: Apache-2.0
"""Home's proposals in the one Othor AI chat (0.26, M26.4) on the sample night: "Chat about
this" opens the chat on the proposal; a question is answered from its evidence only (a reply
with a number that isn't there is not shown); Apply accepts it as Home's Accept does, Decline
declines it; the old panel's link opens the chat."""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa

from othor.ai.gateway import ProviderReply
from othor.core.tenancy import tenant_scope
from othor.engine.findings import pattern_of
from othor.engine.models import Finding, Proposal
from othor.ontology import service
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_home_nights import WINDOW_DETECTORS
from tests.web.test_in_place import _post
from tests.web.test_sweep_web import night  # noqa: F401


def _proposal(svc, cid, rid) -> str:
    with svc.sf() as s, tenant_scope(cid):
        found = [
            r.body
            for r in s.scalars(sa.select(Finding).where(Finding.run_id == rid))
            if r.status == "open" and r.detector in WINDOW_DETECTORS
        ]
        f = sorted(found, key=lambda x: x["fingerprint"])[0]
        p = Proposal(
            kind="suppression",
            text=f"{f['metric']}: the same finding around these dates. Mark it as a known pattern?",
            evidence={
                "years": [2025, 2026],
                "window": f["window"],
                "fingerprint": f["fingerprint"],
                "metric": f["metric"],
                "name": "Recurring dates",
                "confidence": "medium",
            },
            origin={"rule": "recurs_on_calendar_window", "finding": f["fingerprint"]},
            fingerprint=f"suppression:{pattern_of(f)}",
            status="open",
            run_id=rid,
        )
        s.add(p)
        s.commit()
        return str(p.id)


def test_a_proposal_is_discussed_and_applied_in_the_chat(night, monkeypatch):  # noqa: F811
    from othor.web.routes import othor_chat as routes

    env, h, cid, rid, svc = night
    pid = _proposal(svc, cid, rid)
    # Suggest edits on the Business Model page opens this chat (owner, 23 Nov 2026)
    bm = env.client.get(h + "/ontology").text
    assert f"/ontology?panel=othor&amp;subject=proposal:{pid}" in bm
    subject = f"proposal:{pid}"
    back = f"/home?panel=othor&subject={subject}"
    page = env.client.get(h + back).text.split('id="othor-chat"', 1)[1]
    assert "About this proposal" in page and "Mark it as a known pattern?" in page
    assert "Medium confidence" in page.replace("medium", "Medium") and "Decline" in page
    # the old link opens the same chat
    old = env.client.get(h + f"/home?panel=proposal&p={pid}", follow_redirects=False)
    assert old.status_code == 303 and old.headers["location"] == back

    replies = iter(
        [
            {"reply": "It recurred in 2025 and 2026 around the same dates."},
            {"reply": "It saves you 40 hours a week."},  # a number not in the evidence
        ]
    )

    class Fake:
        name, configured = "fake", True

        def call(self, system, messages, schema, max_tokens=0):
            return ProviderReply(json.dumps(next(replies)), "fake", 1, 1)

    monkeypatch.setattr(routes, "provider_for", lambda settings: Fake())
    _post(env, h, "/othor/reply", {"subject": subject, "text": "why?", "back": back})
    _post(env, h, "/othor/reply", {"subject": subject, "text": "and the gain?", "back": back})
    page = env.client.get(h + back).text
    assert "It recurred in 2025 and 2026 around the same dates." in page
    assert "40 hours" not in page and "I can only speak to the evidence shown above." in page
    with svc.sf() as s, tenant_scope(cid):
        n = service.current_number(s)
        assert s.get(Proposal, uuid.UUID(pid)).status == "open"  # talk applies nothing
    _post(env, h, "/othor/apply", {"subject": subject, "back": back})
    with svc.sf() as s, tenant_scope(cid):
        assert s.get(Proposal, uuid.UUID(pid)).status == "accepted"
        assert service.current_number(s) == n + 1
        assert [
            r for r in service.active(s, ["known_pattern"]) if r.source_ref == {"proposal": pid}
        ]
    assert "Applied. It's in your Business Model" in env.client.get(h + back).text.replace(
        "&#39;", "'"
    )


def test_decline_in_the_chat_declines_and_writes_nothing(night):  # noqa: F811
    env, h, cid, rid, svc = night
    pid = _proposal(svc, cid, rid)
    subject = f"proposal:{pid}"
    back = f"/home?panel=othor&subject={subject}"
    env.client.get(h + back)
    with svc.sf() as s, tenant_scope(cid):
        n = service.current_number(s)
    _post(env, h, "/othor/apply", {"subject": subject, "decision": "decline", "back": back})
    with svc.sf() as s, tenant_scope(cid):
        assert s.get(Proposal, uuid.UUID(pid)).status == "declined"
        assert service.current_number(s) == n
    assert "Declined. Nothing changed." in env.client.get(h + back).text
