# SPDX-License-Identifier: Apache-2.0
"""A worker process for test_run_resume: runs queued jobs until OTHOR_TEST_KILL_AT kills it."""

import datetime as dt
import sys
from pathlib import Path

from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.db import make_engine, make_session_factory
from othor.jobs.runner import WorkerContext, run_pending

NOW = dt.datetime(2026, 11, 1, 20, 35, tzinfo=dt.UTC)

if __name__ == "__main__":
    url, data = sys.argv[1], Path(sys.argv[2])
    settings = Settings(env="test", data_dir=data, db_url=url)
    key = InstanceKey.load_or_create(data / "instance.key", allow_create=True)
    sf = make_session_factory(make_engine(url))
    run_pending(WorkerContext(settings, sf, key, clock=lambda: NOW))
