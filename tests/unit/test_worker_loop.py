# SPDX-License-Identifier: Apache-2.0
"""The worker outlives a database that goes away for a moment (#16): a dropped connection is
logged and retried with a growing wait, never the end of the process (the dev worker exited when
Postgres restarted, and no nightly run happened after)."""

from __future__ import annotations

import sqlalchemy.exc as sa_exc

from othor.jobs.runner import loop_step


def _down(*_a, **_k):
    raise sa_exc.OperationalError("SELECT 1", {}, Exception("the database system is shutting down"))


def test_a_dropped_database_is_waited_out_not_fatal() -> None:
    backoff, waits = 0.0, []
    for _ in range(7):
        wait, backoff = loop_step(None, run=_down, backoff=backoff)
        waits.append(wait)
    assert waits == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]  # doubling, capped at 30 s


def test_work_and_idle_as_before() -> None:
    # once the database is back the wait resets
    assert loop_step(None, run=lambda *_a, **_k: 3, backoff=8.0, poll_s=1.0) == (0.0, 0.0)
    assert loop_step(None, run=lambda *_a, **_k: 0, backoff=8.0, poll_s=1.0) == (1.0, 0.0)
