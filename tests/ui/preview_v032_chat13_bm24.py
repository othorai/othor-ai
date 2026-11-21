# SPDX-License-Identifier: Apache-2.0
"""Previews of Chat 1.3 and Business Model 2.4 for the owner. Dev only:

    .venv/bin/python -m pytest tests/ui/preview_v032_chat13_bm24.py -s -p no:cacheprovider \
        -o addopts=""

Needs `make dev && make seed`. Every state is made by code: the setup decision's suggestion is
stored as the draft job stores it (a fake gateway's answer, checked by code); the rebates draft is
the chat's own (a fake model's two changes, checked and computed by code). Desktop (1440) and
phone (390) into ~/Desktop/othor-preview/v0.32/chat13-bm24/."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from othor.core.tenancy import tenant_scope
from othor.ontology import service
from tests.fixtures.sample_company.ontology import T, m, op
from tests.web.board_worlds import PREPS, World, setup_world  # noqa: F401
from tests.web.conftest import BASE, env  # noqa: F401
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_decision_suggest import FakeGateway
from tests.web.test_level_up_web import LINE, _without_rebates
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "v0.32" / "chat13-bm24"
ROOT = Path(__file__).resolve().parents[2]


def _port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


@contextmanager
def _serve(app: Any, client: Any) -> Iterator[tuple[str, list[dict[str, str]]]]:
    import uvicorn

    port = _port()
    server = uvicorn.Server(
        uvicorn.Config(cast(Any, app), host="127.0.0.1", port=port, log_level="warning")
    )
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
    cookies = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain or f"indigothread.{BASE}",
            "path": c.path or "/",
        }
        for c in client.cookies.jar
    ]
    try:
        yield f"http://indigothread.{BASE}:{port}", cookies
    finally:
        server.should_exit = True
        th.join(5)


def _shoot(
    app: str, cookies: list[dict[str, str]], shots: list[tuple[str, str, bool, str]]
) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            for name, path, full, click in shots:
                pg.goto(app + path, wait_until="networkidle")
                if click:
                    pg.click(click)
                pg.wait_for_timeout(800)  # the lazy values arrive
                pg.screenshot(path=str(OUT / f"{name}{tag}.png"), full_page=full)
            ctx.close()
        b.close()


def test_preview_setup_decision(setup_world: World) -> None:  # noqa: F811
    from othor.setup import decision_suggest as ds
    from othor.web import setup_view as v

    subprocess.run([sys.executable, "scripts/build_css.py"], cwd=ROOT, check=True)
    OUT.mkdir(parents=True, exist_ok=True)
    w = setup_world
    PREPS["metrics_drafted"](w)
    gw = FakeGateway(
        {"choice": "source", "why": "Your business message says net revenue excludes GST."}
    )
    ds.suggest_all(w.svc.sf, gw, w.cid)
    with _serve(w.env.app, w.env.client) as (app, cookies):
        _shoot(
            app,
            cookies,
            [
                ("01-setup-decision-suggestion", v.decision_url("net_revenue"), False, ""),
                (
                    "02-setup-decision-suggest-edits",
                    v.decision_url("net_revenue") + "&edit=1",
                    False,
                    "",
                ),
            ],
        )


def test_preview_business_model_levels(night) -> None:  # noqa: F811
    from othor.chat import changes as cc
    from othor.chat import service as chat
    from othor.core.models import Company
    from tests.unit.ontology.test_level_paths import GW

    OUT.mkdir(parents=True, exist_ok=True)
    env_, _h, cid, _rid, svc = night
    _without_rebates(svc, cid)
    with svc.sf() as s, tenant_scope(cid):  # as the demo accounts: no owners
        gone = [service.retire("owner", r.key) for r in service.active(s, ["owner"])]
        service.apply(s, gone, actor="test", reason="No owners")
        s.commit()
    changes = [
        {
            "op": "put",
            "type": "metric",
            "key": "rebates",
            "body_json": json.dumps(
                {
                    "name": "Rebates",
                    "table": T("erp_db", "erp_db", "cost_ledger"),
                    "measure": {"agg": "sum", "expr": {"col": "amount"}},
                    "date": "entry_date",
                    "filters": [op("=", {"col": "account_code"}, {"lit": 4410})],
                    "listed": False,
                }
            ),
        },
        {
            "op": "put",
            "type": "metric",
            "key": "net_revenue",
            "body_json": json.dumps(
                {
                    "formula": op(
                        "-",
                        op("-", m("invoiced_value", "zero"), m("credit_note_value", "zero")),
                        m("rebates", "zero"),
                    ),
                    "description": "Invoiced value minus credit notes and rebates",
                }
            ),
        },
    ]
    with svc.sf() as s, tenant_scope(cid):
        import datetime as dt

        co = s.get(Company, cid)
        t = chat.thread(s, "ontology:metric:net_revenue")
        cc.draft(
            s,
            GW(
                {
                    "changes": changes,
                    "reply": "I'll add Rebates and take it off net revenue.",
                    "clarify": None,
                }
            ),
            co,
            t,
            LINE,
            actor="test",
            today=dt.date(2026, 11, 1),
        )
        s.commit()
    with _serve(env_.app, env_.client) as (app, cookies):
        _shoot(
            app,
            cookies,
            [
                ("03-business-model-level-card", "/ontology", False, ""),
                ("04-business-model-checklist", "/ontology?level=open", True, ""),
                (
                    "05-rebates-one-apply",
                    "/ontology?panel=othor&subject=ontology:metric:net_revenue",
                    False,
                    "",
                ),
                (
                    "06-checklist-sentence-in-chat",
                    "/ontology?panel=othor&subject=ontology&say=The+Head+of+Sales+owns+Net+"
                    "revenue+and+Orders",
                    False,
                    "",
                ),
            ],
        )
    print("previews in", OUT)
