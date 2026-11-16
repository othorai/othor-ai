# SPDX-License-Identifier: Apache-2.0
"""JSON logging with secret redaction.

Never log secrets, tokens or row values from customer sources (CLAUDE.md). The filter is a
safety net; code should not pass such values to a logger in the first place.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

MASK = "***"
SENSITIVE_KEY = re.compile(r"pass(word)?|secret|token|api[_-]?key|authorization|cookie|dsn", re.I)
_PATTERNS = [
    # scheme://user:password@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s]+(@)", re.I), rf"\g<1>{MASK}\g<2>"),
    # ?t=… / &token=… / &code=… / ?invite=… in URLs (access logs): handoff tokens, codes,
    # invitation codes (M11d)
    (re.compile(r"([?&](?:t|token|code|handoff|invite)=)[^&\s\"']+", re.I), rf"\g<1>{MASK}"),
    # /l/<token>, /u/<token> (signed links) and /hooks/postmark/<secret> in paths and URLs (M9)
    (re.compile(r"(/(?:l|u|hooks/postmark)/)[\w-]+"), rf"\g<1>{MASK}"),
    # Healthchecks.io ping URLs (hc-ping.com/<uuid>, self-hosted …/ping/<uuid>): whoever has one
    # can report "all is well" (M11b)
    (
        re.compile(r"((?:hc-ping\.com|/ping)/)[0-9a-f]{8}-[0-9a-f-]{27}", re.I),
        rf"\g<1>{MASK}",
    ),
    # Bearer xyz
    (re.compile(r"(\bbearer\s+)[^\s\"',]+", re.I), rf"\g<1>{MASK}"),
    # key=value / key: value / "key": "value"
    (
        re.compile(
            r"""(["']?\b[\w-]*(?:pass(?:word)?|secret|token|api[_-]?key)\b["']?\s*[:=]\s*["']?)"""
            r"""[^\s"',}]+""",
            re.I,
        ),
        rf"\g<1>{MASK}",
    ),
]
_STD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pat, repl in _PATTERNS:
            value = pat.sub(repl, value)
        return value
    if isinstance(value, Mapping):
        return {
            k: (MASK if isinstance(k, str) and SENSITIVE_KEY.search(k) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(redact(v) for v in value)
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            if isinstance(record.args, Mapping):
                record.args = redact(record.args)
            else:
                args = record.args if isinstance(record.args, tuple) else (record.args,)
                record.args = tuple(redact(a) for a in args)
            try:
                record.msg = redact(record.getMessage())
            except (TypeError, ValueError, KeyError):
                # a library's malformed call: keep the text and the redacted arguments, never raise
                record.msg = redact(f"{record.msg} {record.args!r}")
            record.args = None
        for key in list(vars(record)):
            if key in _STD_ATTRS:
                continue
            val = getattr(record, key)
            setattr(record, key, MASK if SENSITIVE_KEY.search(key) else redact(val))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "at": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key, val in vars(record).items():
            if key not in _STD_ATTRS:
                out[key] = MASK if SENSITIVE_KEY.search(key) else redact(val)
        if record.exc_info:
            out["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Add one redacting JSON handler to the root logger (idempotent; keeps other handlers,
    e.g. pytest's). uvicorn's own loggers are routed to it so access logs are redacted too."""
    root = logging.getLogger()
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.addFilter(RedactingFilter())
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    # httpx/httpcore log every request URL at INFO, and an outbound URL can be a secret (ping
    # URLs): their warnings and errors still come through
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
