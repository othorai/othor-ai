# SPDX-License-Identifier: Apache-2.0
"""Contact us in the app (owner, 17 Nov 2026): "Talk to us to continue" on the trial chip and
"Ask us to raise it" on the AI allowance open the website's form in the side panel, in place,
the owner already known; sent, it is stored and emailed to support with the company's address
and trial. Without the website (self-hosted) the links stay mailto and the panel isn't there."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from othor.core.models import AccessRequest
from tests.needs import needs_site
from tests.web.conftest import make_env, signup

OPEN = {"signup": "open", "trial_days": 14, "support_email": "support@othor.ai"}


@needs_site
def test_the_trial_chip_opens_contact_in_the_panel_and_sends(tmp_path: Path) -> None:
    with make_env(tmp_path, public_site=True, **OPEN) as env:
        signup(env)
        h = env.host("indigothread")
        home = env.client.get(h + "/home").text
        assert 'href="?panel=contact&amp;topic=trial" data-panel>Talk to us to continue' in home
        assert "mailto:support@othor.ai" not in home
        panel = env.client.get(h + "/home?panel=contact&topic=trial").text
        assert 'aria-label="Talk to us"' in panel and "Continuing after the free trial" in panel
        assert 'action="/contact/send"' in panel and "data-inplace" in panel
        assert "From Uday Kamath · uk@indigothread.example · Indigo Thread Co." in panel
        assert 'name="name"' not in panel  # the owner is known
        r = env.client.post(
            h + "/contact/send",
            data={
                "csrf": env.csrf(h + "/home"),
                "topic": "trial",
                "message": "",
                "back": "/home?panel=contact&topic=trial",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/home?panel=contact&topic=trial&sent=1"
        sent = env.client.get(h + r.headers["location"]).text
        assert "Thanks. We’ll write back to uk@indigothread.example." in sent
        mails = [m for m in env.relay.outbox if m.to == "support@othor.ai"]
        assert [m.subject for m in mails] == ["Continue after the trial: Indigo Thread Co."]
        assert mails[0].reply_to == "uk@indigothread.example"
        assert "Trial: 14 days left" in mails[0].text and "indigothread." in mails[0].text
        svc = env.app.state.services
        with svc.sf() as s:
            row = s.scalars(sa.select(AccessRequest)).one()
        assert row.topic == "trial" and row.company_slug == "indigothread"


@needs_site
def test_a_question_needs_a_message_and_a_safe_way_back(tmp_path: Path) -> None:
    with make_env(tmp_path, public_site=True, **OPEN) as env:
        signup(env)
        h = env.host("indigothread")
        csrf = env.csrf(h + "/home")
        r = env.client.post(
            h + "/contact/send",
            data={"csrf": csrf, "topic": "sales", "message": "", "back": "/home"},
            headers={"X-Othor-Inplace": "1"},
        )
        assert r.status_code == 422 and r.headers["X-Othor-Toast"] == "1"
        assert "Write a line about what you need" in r.text
        r = env.client.post(
            h + "/contact/send",
            data={"csrf": csrf, "topic": "sales", "message": "Pricing?", "back": "//evil.example"},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/home?sent=1"


def test_without_the_website_the_links_stay_mail(tmp_path: Path) -> None:
    with make_env(tmp_path, public_site=False, **OPEN) as env:
        signup(env)
        h = env.host("indigothread")
        home = env.client.get(h + "/home?panel=contact&topic=trial").text
        assert 'href="mailto:support@othor.ai">Talk to us to continue' in home
        assert 'aria-label="Talk to us"' not in home
        r = env.client.post(
            h + "/contact/send",
            data={"csrf": env.csrf(h + "/home"), "topic": "trial", "back": "/home"},
            follow_redirects=False,
        )
        assert r.status_code == 404


@needs_site
def test_the_trial_emails_link_to_the_form(tmp_path: Path) -> None:
    from othor.mail.messages import trial_reminder

    with make_env(tmp_path, public_site=True, **OPEN) as env:
        svc = env.app.state.services
        m = trial_reminder(
            to="uk@indigothread.example",
            company_name="Indigo Thread Co.",
            slug="indigothread",
            ends_on="1 Dec",
            settings=svc.settings,
        )
        assert "/contact?topic=trial" in m.text and "support@othor.ai" in m.text
