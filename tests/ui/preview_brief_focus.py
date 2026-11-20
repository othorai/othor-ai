# SPDX-License-Identifier: Apache-2.0
"""Brief focus and honest picks (owner, 20 Nov 2026): previews for the owner. Dev only, run by
hand (needs `make dev`; the sample sources are read through the test fixtures only):

    .venv/bin/python -m pytest tests/ui/preview_brief_focus.py -s -p no:cacheprovider -o addopts=""

On the signed-in 1 Nov world, with a stand-in model (its answers are what brief_draft v4 is asked
to give for indigo2's questions), writes desktop and phone shots to
~/Desktop/othor-preview/brief-focus/:
- a: New brief for the Coimbatore dispatch question: "Not watched yet: dispatch time";
- b: New brief for the receivables question: "Add goal: DSO under 45 days";
- c: the brief list, where the focused briefs now differ from Leadership;
- d: a focused brief with nothing new: its quiet line."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any, cast

from othor.core.tenancy import tenant_scope
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "brief-focus"
COIMBATORE = "Are we shipping metres out of Coimbatore on time, or are dispatch delays building up?"
DSO_Q = "Are receivables over 60 days growing, and are we on track to keep DSO under 45 days?"
DRAFTS = {
    COIMBATORE: {
        "name": "Coimbatore dispatch",
        "question": COIMBATORE,
        "headline": ["metres_dispatched"],
        "missing": ["dispatch time"],
        "targets": [],
    },
    DSO_Q: {
        "name": "Receivables",
        "question": DSO_Q,
        "headline": ["receivables_over_60", "dso"],
        "missing": [],
        "targets": ["DSO under 45 days"],
    },
}


def _fake(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from othor.ai.gateway import ProviderReply
    from othor.web.routes import brief as brief_routes

    class Fake:
        name, configured = "fake", True

        def call(self, system, messages, schema, max_tokens=0):  # type: ignore[no-untyped-def]
            said = json.dumps(messages)
            out = next((d for q, d in DRAFTS.items() if json.dumps(q)[1:-1] in said), None)
            return ProviderReply(json.dumps(out or DRAFTS[DSO_Q]), "fake", 1, 1)

    monkeypatch.setattr(brief_routes, "provider_for", lambda settings: Fake())
    # as a company with Discover sees it (the test world's licence leaves Discover out)
    monkeypatch.setattr(brief_routes, "_discover", lambda: True)


def _brief(s, svc, name: str, headline: list[str]):  # type: ignore[no-untyped-def]
    """A brief with picked numbers and tonight's issue, as the run's own function makes it."""
    from othor.briefs import edit, service, step
    from othor.briefs.models import BriefIssue
    from othor.engine.ledger import sha
    from othor.ontology.compiler.api import canonical
    from othor.web import brief_view as bvw

    run, spec, files = bvw.run_parts(s, svc.settings)
    body = edit.clean(
        {"name": name, "question": f"How is {name.lower()} doing?", "headline": headline},
        spec,
        {},
    )
    b = service.create(s, body, actor="preview", reason="preview")
    s.flush()
    issue = step.preview(files, spec, run.run_date, {**body, "id": str(b.id), "number": 1})
    s.add(
        BriefIssue(
            brief_id=b.id,
            brief_version=1,
            run_id=run.id,
            date=run.run_date,
            status="ready",
            title=issue["title"],
            quiet=bool(issue["quiet"]),
            body=issue,
            issue_hash=sha(canonical(issue).encode()),
            prose={"source": "template"},
        )
    )
    return b, issue


def _quiet_metric(s, svc) -> str:  # type: ignore[no-untyped-def]
    """An active number with no finding tonight on it or what it's computed from."""
    from othor.briefs import scope
    from othor.engine.store import read_json
    from othor.web import brief_view as bvw

    _run, spec, files = bvw.run_parts(s, svc.settings)
    found = read_json(files.outputs / "findings.json") or []
    for k, m in sorted(spec["metrics"].items()):
        if m.get("status") != "active":
            continue
        mine = scope.in_brief(spec, {"headline": [k], "scope": {"metrics": None}})
        if not any(mine(f) for f in found):
            return k
    raise AssertionError("every number has a finding tonight")


def test_preview(night, monkeypatch) -> None:  # noqa: F811
    env, _h, cid, _rid, svc = night
    _fake(monkeypatch)
    with svc.sf() as s, tenant_scope(cid):
        _rec, rec_issue = _brief(s, svc, "Receivables", ["receivables_over_60", "dso"])
        _disp, disp_issue = _brief(s, svc, "Dispatch", ["dispatch_time"])
        q = _quiet_metric(s, svc)
        quiet, quiet_issue = _brief(s, svc, "Quiet numbers", [q])
        s.commit()
        quiet_id = quiet.id
    assert quiet_issue.get("focus_note"), quiet_issue
    print("receivables items:", [i["metric"] for i in rec_issue["items"]])
    print("dispatch items:", [i["metric"] for i in disp_issue["items"]])
    print("quiet line:", quiet_issue["focus_note"])
    OUT.mkdir(parents=True, exist_ok=True)
    import uvicorn

    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = int(so.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(cast(Any, env.app), host="127.0.0.1", port=port, log_level="warning")
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
        for c in env.client.cookies.jar
    ]
    base = f"http://indigothread.{BASE}:{port}"
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            for name, question in (("a-not-watched-yet", COIMBATORE), ("b-add-goal", DSO_Q)):
                pg.goto(base + "/brief/new", wait_until="networkidle")
                pg.fill("#f-question", question)
                pg.click("button.nb-go")
                pg.wait_for_selector("text=Review the draft")
                pg.wait_for_load_state("networkidle")
                pg.screenshot(path=str(OUT / f"{name}{tag}.png"))
                pg.locator(".panel").first.screenshot(path=str(OUT / f"{name}-panel{tag}.png"))
            pg.goto(base + "/brief", wait_until="networkidle")
            pg.screenshot(path=str(OUT / f"c-brief-list{tag}.png"), full_page=True)
            pg.goto(base + f"/brief/{quiet_id}", wait_until="networkidle")
            pg.screenshot(path=str(OUT / f"d-quiet-line{tag}.png"), full_page=True)
            ctx.close()
        b.close()
    server.should_exit = True
    th.join(5)
    print("previews in", OUT)
