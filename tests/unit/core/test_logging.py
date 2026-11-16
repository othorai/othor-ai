# SPDX-License-Identifier: Apache-2.0
import json
import logging

import pytest

from othor.core.logging import JsonFormatter, RedactingFilter, redact


@pytest.mark.parametrize(
    ("raw", "hidden"),
    [
        ("connect postgresql://othor_ro:s3cr3t@db:5432/sales_dw", "s3cr3t"),
        ("password=hunter2 user=x", "hunter2"),
        ("token: abc.def.ghi", "abc.def.ghi"),
        ('{"api_key": "sk-123"}', "sk-123"),
        ("Authorization: Bearer eyJhbGciOi", "eyJhbGciOi"),
        ('"GET /auth/handoff?t=Hdagph65UyUq HTTP/1.1" 303', "Hdagph65UyUq"),
        ("/login/code?code=482913&x=1", "482913"),
        # signed links and the mail webhook carry their secret in the path (M9)
        ('"GET /l/6mZEPKCJyEmjilKLnyq/finding/ab12 HTTP/1.1" 200', "6mZEPKCJyEmjilKLnyq"),
        ('"POST /u/8BXit9Oeb0IThA-6N9J HTTP/1.1" 200', "8BXit9Oeb0IThA-6N9J"),
        ("https://indigothread.othor.ai/l/ADOCUXsqI_n4-h1", "ADOCUXsqI_n4-h1"),
        ('"POST /hooks/postmark/hook-secret-1 HTTP/1.1" 200', "hook-secret-1"),
    ],
)
def test_redact_strings(raw: str, hidden: str) -> None:
    out = redact(raw)
    assert hidden not in out
    assert "***" in out


def test_redact_mapping_by_key() -> None:
    out = redact({"host": "db", "password": "p", "nested": {"client_secret": "c"}})
    assert out == {"host": "db", "password": "***", "nested": {"client_secret": "***"}}


def test_filter_redacts_message_args_and_extra(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("othor.test.redact")
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    rec = log.makeRecord(
        "x",
        logging.INFO,
        __file__,
        1,
        "dsn %s",
        ("mysql://u:pw@h/erp_db",),
        None,
        extra={"secret": "zzz"},
    )
    RedactingFilter().filter(rec)
    line = JsonFormatter().format(rec)
    assert "pw@" not in line
    assert "zzz" not in line
    assert json.loads(line)["msg"].startswith("dsn mysql://u:***@h")


def test_filter_never_raises_on_a_malformed_call() -> None:
    """A library logging args its message has no placeholder for: kept, redacted, no exception."""
    f = RedactingFilter()
    rec = logging.makeLogRecord(
        {"msg": "table loaded", "args": ("password=hunter2",), "levelno": logging.DEBUG}
    )
    assert f.filter(rec)
    assert "hunter2" not in rec.getMessage() and "table loaded" in rec.getMessage()
    rec = logging.makeLogRecord({"msg": "%(user)s in", "args": ({"user": "token=abc123"},)})
    assert f.filter(rec)
    assert "abc123" not in rec.getMessage()


def test_ping_urls_never_reach_the_log() -> None:
    """A Healthchecks.io ping URL is a secret: whoever has it can report "all is well" (M11b).
    httpx logs every request URL at INFO; found in the worker log on 11 Nov."""
    uuid = "1f2e3d4c-5b6a-7980-a1b2-c3d4e5f60718"
    for url in (f"https://hc-ping.com/{uuid}", f"https://hc.example.in/ping/{uuid}/fail"):
        line = f'HTTP Request: GET {url} "HTTP/1.1 200 OK"'
        assert uuid not in redact(line)


def test_http_client_request_lines_are_quiet() -> None:
    from othor.core.logging import configure_logging

    configure_logging()
    for name in ("httpx", "httpcore"):
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)
