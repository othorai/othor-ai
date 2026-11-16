# SPDX-License-Identifier: Apache-2.0
"""M15.1: a source step that fails keeps the driver's error code with the exception's name (the
×35 first night said only "OperationalError"), never the message, which can quote SQL."""

from __future__ import annotations

import sqlalchemy as sa

from othor.engine.orchestrator import _soft


class _MySQLError(Exception):
    pass


class _PgError(Exception):
    sqlstate = "57014"  # query_canceled (statement_timeout)


def _raise(orig: Exception):
    def fn():
        raise sa.exc.OperationalError("SELECT secret_column FROM t", {}, orig)

    return fn


def test_mysql_code_kept_message_not() -> None:
    out = _soft(_raise(_MySQLError(3024, "Query execution was interrupted, maximum ... exceeded")))
    assert not out.ok
    assert out.detail == {"reason": "OperationalError", "code": "3024"}


def test_postgres_sqlstate_kept() -> None:
    out = _soft(_raise(_PgError("canceling statement due to statement timeout")))
    assert out.detail == {"reason": "OperationalError", "code": "57014"}


def test_no_code_no_key() -> None:
    def fn():
        raise TimeoutError("slow")

    assert _soft(fn).detail == {"reason": "TimeoutError"}
