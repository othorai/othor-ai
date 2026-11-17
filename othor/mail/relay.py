# SPDX-License-Identifier: Apache-2.0
"""Mail relay abstraction: console (dev), memory (tests), smtp (Mailpit in dev, any relay
on-premise), postmark (the cloud, decided 6 Nov 2026). Message bodies and codes are never logged.

`send` returns the message id the relay knows the message by (Postmark's MessageID, or our own
Message-ID header), stored on the delivery row so a bounce can find it. A recipient the relay
refuses outright raises `Bounced`; any other failure raises `MailSendError`.
"""

from __future__ import annotations

import base64
import logging
import smtplib
import ssl
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

import httpx

from othor.core.config import Settings

log = logging.getLogger(__name__)


class MailConfigError(RuntimeError):
    pass


class MailSendError(RuntimeError):
    """The relay couldn't take the message; the job retries."""


class Bounced(MailSendError):
    """The relay refused this recipient for good (unknown address, inactive recipient)."""


@dataclass(frozen=True)
class InlineImage:
    """A PNG shown in the HTML as <img src="cid:{cid}"> (charts, the logo): attached, never a
    hosted URL, so on-premise needs nothing public."""

    cid: str
    data: bytes
    filename: str = ""


@dataclass(frozen=True)
class Attachment:
    """A file attached to the message (a report's PDF): not shown inline, kept by the reader."""

    filename: str
    data: bytes
    mime: str = "application/pdf"


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    html: str
    text: str
    sender: str
    reply_to: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    images: tuple[InlineImage, ...] = ()
    files: tuple[Attachment, ...] = ()
    message_id: str = field(default_factory=lambda: make_msgid(domain="othor.ai"))

    def to_email(self) -> EmailMessage:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = self.sender, self.to, self.subject
        m["Message-ID"] = self.message_id
        if self.reply_to:
            m["Reply-To"] = self.reply_to
        for k, v in self.headers.items():
            m[k] = v
        m.set_content(self.text)
        m.add_alternative(self.html, subtype="html")
        if self.images:
            html_part = m.get_payload()[-1]  # type: ignore[index]
            for img in self.images:
                html_part.add_related(
                    img.data,
                    maintype="image",
                    subtype="png",
                    cid=f"<{img.cid}>",
                    filename=img.filename or f"{img.cid.split('@')[0]}.png",
                    disposition="inline",
                )
        for f in self.files:
            main, sub = f.mime.split("/", 1)
            m.add_attachment(f.data, maintype=main, subtype=sub, filename=f.filename)
        return m


class Relay(Protocol):
    def send(self, msg: Message) -> str: ...


class MemoryRelay:
    """Tests: keeps every message. `refuse` holds addresses to bounce, `fail` makes every send
    fail (the relay is down)."""

    def __init__(self) -> None:
        self.outbox: list[Message] = []
        self.refuse: set[str] = set()
        self.fail = False

    def send(self, msg: Message) -> str:
        if self.fail:
            raise MailSendError("relay down")
        if msg.to in self.refuse:
            raise Bounced("550 no such user")
        self.outbox.append(msg)
        return msg.message_id


class ConsoleRelay:
    """Dev only: writes the full message to OTHOR_DATA_DIR/mail/*.eml and logs one line
    without the subject or body (the subject carries the code)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def send(self, msg: Message) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{uuid.uuid4().hex}.eml"
        path.write_bytes(bytes(msg.to_email()))
        log.info("mail written", extra={"to_domain": msg.to.split("@")[-1], "file": path.name})
        return msg.message_id


class SmtpRelay:
    def __init__(self, url: str) -> None:
        u = urlparse(url)
        if u.scheme not in ("smtp", "smtps", "smtp+starttls"):
            raise MailConfigError("OTHOR_SMTP_URL must be smtp://, smtps:// or smtp+starttls://")
        self.host = u.hostname or "localhost"
        self.tls = u.scheme == "smtps"
        self.starttls = u.scheme == "smtp+starttls"
        self.port = u.port or (465 if self.tls else 587 if self.starttls else 25)
        self.user = unquote(u.username) if u.username else None
        self._password = unquote(u.password) if u.password else None

    def __repr__(self) -> str:
        return f"SmtpRelay({self.host}:{self.port}, tls={self.tls or self.starttls})"

    def send(self, msg: Message) -> str:
        ctx = ssl.create_default_context()
        cls = smtplib.SMTP_SSL if self.tls else smtplib.SMTP
        kw = {"context": ctx} if self.tls else {}
        try:
            with cls(self.host, self.port, timeout=20, **kw) as smtp:  # type: ignore[arg-type]
                if self.starttls:
                    smtp.starttls(context=ctx)
                if self.user and self._password:
                    smtp.login(self.user, self._password)
                smtp.send_message(msg.to_email())
        except smtplib.SMTPRecipientsRefused as e:
            codes = [c for c, _ in e.recipients.values()]
            if codes and all(500 <= c < 600 for c in codes):
                raise Bounced(f"{codes[0]} recipient refused") from None
            raise MailSendError(f"{codes[0] if codes else '?'} recipient refused") from None
        except (smtplib.SMTPException, OSError) as e:
            raise MailSendError(type(e).__name__) from None
        return msg.message_id


POSTMARK_URL = "https://api.postmarkapp.com/email"
POSTMARK_BOUNCED = {300, 406}  # invalid address · inactive recipient (bounced or complained)


class PostmarkRelay:
    """Postmark's email API through the one allowlisted HTTP client. Opens and links aren't
    tracked: the email carries business data and nothing else."""

    def __init__(
        self,
        token: str,
        stream: str = "outbound",
        client: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._token = token
        self.stream = stream
        self._client = client

    def __repr__(self) -> str:
        return f"PostmarkRelay(stream={self.stream})"

    def payload(self, msg: Message) -> dict[str, Any]:
        body: dict[str, Any] = {
            "From": msg.sender,
            "To": msg.to,
            "Subject": msg.subject,
            "HtmlBody": msg.html,
            "TextBody": msg.text,
            "MessageStream": self.stream,
            "TrackOpens": False,
            "TrackLinks": "None",
            "Headers": [{"Name": "Message-ID", "Value": msg.message_id}]
            + [{"Name": k, "Value": v} for k, v in msg.headers.items()],
        }
        if msg.reply_to:
            body["ReplyTo"] = msg.reply_to
        if msg.images:
            body["Attachments"] = [
                {
                    "Name": img.filename or f"{img.cid.split('@')[0]}.png",
                    "Content": base64.b64encode(img.data).decode(),
                    "ContentType": "image/png",
                    "ContentID": f"cid:{img.cid}",
                }
                for img in msg.images
            ]
        if msg.files:
            body.setdefault("Attachments", []).extend(
                {
                    "Name": f.filename,
                    "Content": base64.b64encode(f.data).decode(),
                    "ContentType": f.mime,
                }
                for f in msg.files
            )
        return body

    def send(self, msg: Message) -> str:
        from othor.core.http import outbound_client

        make = self._client or outbound_client
        try:
            with make() as client:
                r = client.post(
                    POSTMARK_URL,
                    json=self.payload(msg),
                    headers={
                        "Accept": "application/json",
                        "X-Postmark-Server-Token": self._token,
                    },
                )
        except httpx.HTTPError as e:
            raise MailSendError(type(e).__name__) from None
        try:
            data = r.json()
        except ValueError:
            raise MailSendError(f"postmark {r.status_code}") from None
        code = int(data.get("ErrorCode") or 0)
        if r.status_code == 200 and code == 0:
            return str(data["MessageID"])
        if code in POSTMARK_BOUNCED:
            raise Bounced(f"postmark {code}")
        raise MailSendError(f"postmark {r.status_code}/{code}")


def get_relay(settings: Settings) -> Relay:
    kind = settings.mail_relay
    if kind == "console":
        if settings.env == "prod":
            raise MailConfigError("the console relay is for dev only; set OTHOR_MAIL_RELAY=smtp")
        return ConsoleRelay(settings.data_dir / "mail")
    if kind == "memory":
        return MemoryRelay()
    if kind == "smtp":
        return SmtpRelay(settings.smtp_url)
    return PostmarkRelay(settings.postmark_token.get_secret_value(), settings.postmark_stream)
