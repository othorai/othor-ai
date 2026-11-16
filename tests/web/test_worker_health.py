# SPDX-License-Identifier: Apache-2.0
"""When the worker stops, the owner sees why the page is empty, and monitors can tell (M11b §6,
after #16): the "Nightly runs are paused" banner and `/healthz/worker`."""

from __future__ import annotations

import pytest

from othor.jobs import heartbeat
from tests.web.conftest import ROOT, Env, signup

PAUSED = "Nightly runs are paused"


def _beat(env: Env) -> None:
    svc = env.app.state.services  # type: ignore[attr-defined]
    heartbeat.beat(svc.sf, env.clock())


def test_healthz_worker_reports_the_age(env: Env) -> None:
    r = env.client.get(f"{ROOT}/healthz/worker")
    assert r.status_code == 503 and r.json()["status"] == "never"
    _beat(env)
    env.clock.tick(minutes=3)
    r = env.client.get(f"{ROOT}/healthz/worker")
    assert r.status_code == 200 and r.json() == {"status": "ok", "heartbeat_age_s": 180}
    env.clock.tick(minutes=8)
    r = env.client.get(f"{ROOT}/healthz/worker")
    assert r.status_code == 503 and r.json() == {"status": "stale", "heartbeat_age_s": 660}
    # the load balancer's check is the web only: a stopped worker never drains the web target
    assert env.client.get(f"{ROOT}/healthz").status_code == 200


@pytest.mark.parametrize("path", ["/home", "/brief", "/sweep"])
def test_a_stale_heartbeat_shows_the_banner(env: Env, path: str) -> None:
    signup(env)
    h = env.host("indigothread")
    _beat(env)
    assert PAUSED not in env.client.get(h + path).text
    env.clock.tick(minutes=11)
    page = env.client.get(h + path).text
    assert PAUSED in page and "Othor AI has been told." in page
    # in the company's zone (IST): 9:00 AM UTC is 14:30
    assert "since Wed 4 Nov 2:30 PM" in page


def test_the_banner_stays_off_other_pages(env: Env) -> None:
    signup(env)
    _beat(env)
    env.clock.tick(hours=2)
    assert PAUSED not in env.client.get(env.host("indigothread") + "/sources").text
