# SPDX-License-Identifier: Apache-2.0
"""Server time per page under 300 ms (M11a, docs/10 M11), read from each response's
Server-Timing header on the steady 1 Nov night. Median of 5 after a warm-up. Needs
`make dev && make seed`."""

from __future__ import annotations

import re
import statistics

import pytest

from tests.needs import HAS_SITE
from tests.web.test_brief_web import _brief_id
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import _fp, night  # noqa: F401

BUDGET_MS = 300.0
pytestmark = pytest.mark.perf


def _server_ms(r) -> float:  # type: ignore[no-untyped-def]
    m = re.search(r"app;dur=([0-9.]+)", r.headers["server-timing"])
    assert m, r.headers.get("server-timing")
    return float(m.group(1))


def test_every_page_is_under_budget(night):  # noqa: F811
    env, h, cid, rid, svc = night
    bid = _brief_id(svc, cid)
    fp = _fp(svc, cid, rid, "net_hiding_gross")
    pages = [
        "/home",
        "/home?panel=questions",
        "/brief",
        f"/brief/{bid}",
        f"/brief/{bid}?view=full",
        f"/brief/{bid}/finding/{fp}",
        "/brief/archive",
        f"/brief/{bid}/edit",
        "/sweep",
        "/sweep?tab=watching",
        "/sweep?tab=suppressed",
        "/sweep?tab=resolved",
        f"/sweep/finding/{fp}",
        f"/sweep/finding/{fp}?tab=test",
        "/ontology",
        "/ontology?panel=history",
        "/ontology?metric=net_revenue",
        "/sources",
        "/settings",
    ]
    slow = {}
    for path in pages:
        r = env.client.get(h + path)
        assert r.status_code == 200, (path, r.status_code)
        times = [_server_ms(env.client.get(h + path)) for _ in range(5)]
        med = statistics.median(times)
        if med > BUDGET_MS:
            slow[path] = round(med)
    assert slow == {}, f"over {BUDGET_MS:.0f} ms: {slow}"


def test_root_pages_are_fast(night):  # noqa: F811
    env, *_ = night
    env.app.state.services.settings.public_site = True  # the website pages (M11a, M12b)
    site = ("/", "/ips", "/ips.txt", "/security") if HAS_SITE else ()  # the website plug-in's
    for path in ("/login", "/signup", *site):
        assert env.client.get("http://othor.localhost" + path).status_code == 200, path
        times = [_server_ms(env.client.get("http://othor.localhost" + path)) for _ in range(5)]
        assert statistics.median(times) < 50, path
