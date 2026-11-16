# SPDX-License-Identifier: Apache-2.0
"""The relays (decided 6 Nov 2026): Postmark's email API through the one allowlisted client,
inline images as ContentID attachments, no open or link tracking; an inactive or invalid
recipient is a bounce; SMTP 5xx at RCPT is a bounce; settings refuse Postmark without its
secrets; the token is never in a repr."""

from __future__ import annotations

import base64
import json
import smtplib

import httpx
import pytest

from othor.core.config import Settings
from othor.core.http import ALLOWED_HOSTS, HostNotAllowed, outbound_client
from othor.mail import relay as mail
from othor.mail.relay import Bounced, InlineImage, MailSendError, Message, PostmarkRelay


def _msg() -> Message:
    return Message(
        to="priya@indigothread.example",
        subject="South fell ₹1.4 Cr; West’s rise hid it · Leadership",
        html='<img src="cid:logo@othor.ai">',
        text="text",
        sender="Othor AI <brief@indigothread.othor.ai>",
        reply_to="uk@indigothread.example",
        headers={"List-Unsubscribe": "<https://indigothread.othor.ai/u/t>"},
        images=(InlineImage("logo@othor.ai", b"\x89PNG", "othor-ai.png"),),
    )


def _relay(handler) -> PostmarkRelay:
    transport = httpx.MockTransport(handler)
    return PostmarkRelay("tok-123", client=lambda: outbound_client(transport))


def test_postmark_payload_and_message_id():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["token"] = req.headers["X-Postmark-Server-Token"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ErrorCode": 0, "MessageID": "pm-42"})

    assert _relay(handler).send(_msg()) == "pm-42"
    assert seen["url"] == "https://api.postmarkapp.com/email" and seen["token"] == "tok-123"
    b = seen["body"]
    assert (
        b["From"] == "Othor AI <brief@indigothread.othor.ai>"
        and b["ReplyTo"] == "uk@indigothread.example"
    )
    assert b["TrackOpens"] is False and b["TrackLinks"] == "None"
    assert b["MessageStream"] == "outbound"
    (att,) = b["Attachments"]
    assert (
        att["ContentID"] == "cid:logo@othor.ai" and base64.b64decode(att["Content"]) == b"\x89PNG"
    )
    assert {"Name": "List-Unsubscribe", "Value": "<https://indigothread.othor.ai/u/t>"} in b[
        "Headers"
    ]


@pytest.mark.parametrize(("code", "err"), [(406, Bounced), (300, Bounced), (10, MailSendError)])
def test_postmark_errors(code, err):
    relay = _relay(lambda req: httpx.Response(422, json={"ErrorCode": code, "Message": "x"}))
    with pytest.raises(err):
        relay.send(_msg())


def test_postmark_down_is_a_send_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(MailSendError):
        _relay(handler).send(_msg())


def test_postmark_goes_through_the_allowlist():
    assert "api.postmarkapp.com" in ALLOWED_HOSTS
    client = outbound_client(httpx.MockTransport(lambda r: httpx.Response(200)))
    with client as c, pytest.raises(HostNotAllowed):
        c.post("https://api.sendgrid.com/v3/mail/send")


def test_the_token_never_shows():
    r = PostmarkRelay("secret-token-xyz")
    assert "secret-token-xyz" not in repr(r)


def test_settings_refuse_postmark_without_its_secrets():
    with pytest.raises(ValueError, match="POSTMARK"):
        Settings(_env_file=None, mail_relay="postmark")  # type: ignore[call-arg]
    st = Settings(
        _env_file=None,  # type: ignore[call-arg]
        mail_relay="postmark",
        postmark_token="t",
        postmark_webhook_secret="w",
    )
    assert isinstance(mail.get_relay(st), PostmarkRelay)


@pytest.mark.parametrize(
    ("code", "text", "bounce"), [(550, b"no such user", True), (451, b"try later", False)]
)
def test_smtp_5xx_at_rcpt_is_a_bounce_and_4xx_a_retry(monkeypatch, code, text, bounce):
    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send_message(self, m):
            raise smtplib.SMTPRecipientsRefused({"priya@indigothread.example": (code, text)})

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    with pytest.raises(MailSendError) as e:
        mail.SmtpRelay("smtp://localhost:1026").send(_msg())
    assert isinstance(e.value, Bounced) is bounce


def test_inline_images_are_related_parts_with_their_cid():
    m = _msg().to_email()
    parts = [p for p in m.walk() if p.get_content_type() == "image/png"]
    assert len(parts) == 1 and parts[0]["Content-ID"] == "<logo@othor.ai>"
    assert m["Message-ID"]
