# SPDX-License-Identifier: Apache-2.0
"""#56 (13 Nov 2026): a source's name on Data sources ("PostgreSQL · sales_dw") is in the same
size, weight and colour as a metric's name in the Ontology."""

from __future__ import annotations

import pytest

from tests.ui.test_capture_states import serve, session_cookie
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

pytestmark = pytest.mark.browser
LOOK = "e => { const s = getComputedStyle(e); return [s.fontSize, s.fontWeight, s.color]; }"


def test_a_source_name_reads_as_a_metric_name(night) -> None:  # noqa: F811
    sync_api = pytest.importorskip("playwright.sync_api")
    env, *_ = night
    name, val = session_cookie(env).split("=", 1)
    with serve(env.app) as port, sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_cookies(
            [{"name": name, "value": val, "domain": f"indigothread.{BASE}", "path": "/"}]
        )
        pg = ctx.new_page()
        pg.goto(f"http://indigothread.{BASE}:{port}/ontology")
        metric = pg.eval_on_selector(".ont-section .metric-link", LOOK)
        pg.goto(f"http://indigothread.{BASE}:{port}/sources")
        source = pg.eval_on_selector(".src-name", LOOK)
        b.close()
    assert source == metric
