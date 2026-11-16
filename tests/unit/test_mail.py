# SPDX-License-Identifier: Apache-2.0
"""Mail relay and the sign-in code email (8a)."""

import logging
from pathlib import Path

import pytest

from othor.core.config import Settings
from othor.mail import relay as mail
from othor.mail.messages import sign_in_code


def _settings(**kw: str) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_sign_in_code_email() -> None:
    msg = sign_in_code(
        to="uk@indigothread.example",
        code="482913",
        company_name="Indigo Thread Co.",
        slug="indigothread",
        settings=_settings(mail_from_domain="othor.ai"),
    )
    assert msg.subject == "Your Othor AI sign-in code: 482 913"
    assert msg.sender == '"Indigo Thread Co. · Othor AI" <brief@othor.ai>'
    for part in (msg.html, msg.text):
        assert "482 913" in part and "Indigo Thread Co." in part
        assert "Expires in 10 minutes." in part
    assert "Didn’t request it? Ignore this email." in msg.html
    assert "<table" in msg.html and 'width="640"' in msg.html  # one column, 640 px


def test_signup_email_without_a_company_yet() -> None:
    msg = sign_in_code(
        to="new@acme.in",
        code="000123",
        company_name="Acme Fabrics",
        slug=None,
        settings=_settings(mail_from_domain="othor.ai"),
    )
    assert msg.sender == "Othor AI <hello@othor.ai>"
    assert "000 123" in msg.text


def test_sender_is_one_address_with_the_company_in_the_name() -> None:
    """M11b decision A: every company sends from brief@<mail domain> (one verified domain at
    Postmark, no per-company DNS); the display name says whose brief it is. A name with
    punctuation is quoted, so it can't add an address."""
    from othor.mail.messages import sender

    st = _settings(mail_from_domain="othor.ai")
    assert sender(st, "Indigo Thread Co.") == '"Indigo Thread Co. · Othor AI" <brief@othor.ai>'
    assert sender(st, None) == "Othor AI <hello@othor.ai>"
    odd = sender(st, 'Rao, Shah & "Sons" <x@evil.example>')
    from email.utils import getaddresses

    assert getaddresses([odd]) == [
        ('Rao, Shah & "Sons" <x@evil.example> · Othor AI', "brief@othor.ai")
    ]


def test_memory_relay_and_factory() -> None:
    r = mail.get_relay(_settings(mail_relay="memory"))
    assert isinstance(r, mail.MemoryRelay)
    msg = sign_in_code(to="a@b.in", code="111222", company_name="B", slug="b", settings=_settings())
    r.send(msg)
    assert r.outbox == [msg]


def test_console_relay_refused_in_prod() -> None:
    with pytest.raises(mail.MailConfigError, match="console"):
        mail.get_relay(_settings(mail_relay="console", env="prod", secret_key="x" * 40))


def test_console_relay_never_logs_the_code(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    r = mail.get_relay(_settings(mail_relay="console", data_dir=str(tmp_path)))
    msg = sign_in_code(to="a@b.in", code="482913", company_name="B", slug="b", settings=_settings())
    with caplog.at_level(logging.DEBUG):
        r.send(msg)
    out = capsys.readouterr()
    assert "482" not in caplog.text and "482 913" not in out.out + out.err
    saved = list((tmp_path / "mail").glob("*.eml"))
    assert len(saved) == 1 and "482 913" in saved[0].read_text()  # dev copy on disk only


def test_smtp_relay_parses_url() -> None:
    r = mail.get_relay(_settings(mail_relay="smtp", smtp_url="smtp://mailpit:1025"))
    assert isinstance(r, mail.SmtpRelay)
    assert (r.host, r.port, r.tls) == ("mailpit", 1025, False)
    r2 = mail.get_relay(_settings(mail_relay="smtp", smtp_url="smtps://u:p@relay.example:465"))
    assert isinstance(r2, mail.SmtpRelay) and r2.tls and r2.user == "u"
    assert "p@" not in repr(r2)
