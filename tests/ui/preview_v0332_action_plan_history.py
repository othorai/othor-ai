# SPDX-License-Identifier: Apache-2.0
"""Recently resolved, the Action plan's history (owner, 22 Nov 2026): the section at the foot of the
Action plan page with each kind of reason (the numbers cleared it, a goal no longer off track,
muted, snoozed, a watch stopped), Show all, Home's "N resolved this week" line, and the empty
state. Dev only, run by hand (needs `make dev && make seed`):

    .venv/bin/python -m pytest tests/ui/preview_v0332_action_plan_history.py -s \\
        -p no:cacheprovider -o addopts=""

Writes ~/Desktop/othor-preview/v0.33.2/action-plan-history/ (desktop and phone)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from othor.briefs.models import BriefIssue
from othor.core.tenancy import tenant_scope
from tests.ui.preview_v033_what_to_do import _serve
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "v0.33.2" / "action-plan-history"


def _finding_action(fp: str, text: str, title: str) -> dict[str, Any]:
    return {
        "kind": "finding",
        "key": f"finding:{fp}",
        "fingerprints": [fp],
        "text": text,
        "why": [{"title": title, "fingerprint": fp}],
    }


def _world(svc: Any, cid: Any) -> None:
    """Two earlier issues (6 and 3 days before today's) whose actions have since gone, each for a
    different reason; the rows stay (the night fixture is this module's only)."""
    from othor.engine.models import Finding, Mute, Run, Snooze, Watch

    with svc.sf() as s, tenant_scope(cid):
        today = s.scalars(sa.select(BriefIssue)).one()
        run = s.get(Run, today.run_id)
        aware = dt.datetime.combine(today.date, dt.time(9), tzinfo=dt.UTC)
        w = Watch(
            company_id=cid,
            workspace_id=cid,
            label="Receivables over 60 days, share",
            indicator={"metric": "receivables_over_60", "kind": "value"},
            threshold="0.12",
            direction="above",
            owner_name="Leela Tandon",
            since=today.date - dt.timedelta(days=20),
            status="breached",
            stopped_at=aware - dt.timedelta(days=2),
        )
        s.add(w)
        s.flush()
        days = {6: [], 3: []}  # days before today -> actions
        days[6] += [
            {
                "kind": "goal",
                "key": "goal:dso_under_45",
                "fingerprints": [],
                "text": "Bring DSO down by 5 days by 31 Mar: at today’s pace it ends at 50 days.",
                "why": [{"title": "DSO · 45 d target by 31 Mar", "goal": "dso_under_45"}],
            },
            _finding_action(
                "pv_cleared",
                "Call Ravira Exports to find out why they stopped invoicing on 9 Oct.",
                "Ravira Exports stopped invoicing on 9 Oct",
            ),
            _finding_action(
                "pv_old1",
                "Find out what changed in the week of 5 Oct, when receivables stepped up.",
                "Receivables over 60 days stepped up in the week of 5 Oct",
            ),
        ]
        days[3] += [
            _finding_action(
                "pv_muted",
                "Win orders beyond your biggest customers: four of them hold 65% of the book.",
                "Four customers now hold 65% of the order book",
            ),
            _finding_action(
                "pv_snoozed",
                "Have someone call the 16 customers still marked active who haven’t invoiced "
                "in 90+ days.",
                "16 customers still marked active haven’t invoiced in 90+ days",
            ),
            {
                "kind": "watch",
                "key": f"watch:{w.id}",
                "fingerprints": [],
                "text": "Ask Leela Tandon why receivables over 60 days, share reads 16%, above "
                "the 12% limit you set.",
                "why": [{"title": "Receivables over 60 days, share", "watch": str(w.id)}],
            },
        ]
        for back, acts in days.items():
            day = today.date - dt.timedelta(days=back)
            r = Run(
                company_id=cid,
                workspace_id=cid,
                number=800 + back,
                kind="nightly",
                run_date=day,
                spec_id=run.spec_id,
                image_version="preview",
                started_at=run.started_at,
                status="done",
            )
            s.add(r)
            s.flush()
            s.add(
                BriefIssue(
                    company_id=cid,
                    workspace_id=cid,
                    brief_id=today.brief_id,
                    brief_version=today.brief_version,
                    run_id=r.id,
                    date=day,
                    status="ready",
                    title="Earlier",
                    body={"actions": acts},
                    issue_hash="p" * 64,
                    prose={},
                )
            )
        last = s.scalar(sa.select(Run).where(Run.number == 803))
        for fp, back, reason in (
            ("pv_cleared", 1, "Cleared by data"),
            ("pv_old1", 2, "Explained by your answer (30 Oct)"),
        ):
            s.add(
                Finding(
                    company_id=cid,
                    workspace_id=cid,
                    run_id=last.id,
                    fingerprint=fp,
                    detector="missing_records",
                    metric="invoiced_value",
                    breakdown="",
                    state="measured",
                    status="resolved",
                    change="Resolved",
                    first_seen_run=800,
                    days_open=4,
                    body={
                        "status": "resolved",
                        "resolved": {
                            "date": (today.date - dt.timedelta(days=back)).isoformat(),
                            "reason": reason,
                        },
                    },
                )
            )
        s.add(
            Mute(
                company_id=cid,
                workspace_id=cid,
                pattern="pv",
                fingerprint="pv_muted",
                label="",
                actor="owner",
                created_at=aware - dt.timedelta(days=1),
            )
        )
        s.add(
            Snooze(
                company_id=cid,
                workspace_id=cid,
                fingerprint="pv_snoozed",
                until=today.date + dt.timedelta(days=6),
                label="",
                actor="owner",
                created_at=aware - dt.timedelta(days=1),
            )
        )
        s.commit()


def _clear(svc: Any, cid: Any) -> None:
    from othor.engine.models import Finding, Mute, Run, Snooze, Watch

    with svc.sf() as s, tenant_scope(cid):
        old = list(s.scalars(sa.select(Run.id).where(Run.number >= 800)))
        s.execute(sa.delete(Finding).where(Finding.run_id.in_(old)))
        s.execute(sa.delete(BriefIssue).where(BriefIssue.run_id.in_(old)))
        s.execute(sa.delete(Run).where(Run.id.in_(old)))
        s.execute(sa.delete(Mute).where(Mute.fingerprint == "pv_muted"))
        s.execute(sa.delete(Snooze).where(Snooze.fingerprint == "pv_snoozed"))
        s.execute(sa.delete(Watch).where(Watch.label == "Receivables over 60 days, share"))
        s.commit()


def test_preview_action_plan_history(night) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    env, _h, cid, _rid, svc = night
    _world(svc, cid)
    with _serve(env.app, env.client) as (app, cookies), sync_playwright() as pw:
        br = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = br.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            pg.goto(app + "/action-plan", wait_until="networkidle")
            pg.wait_for_timeout(400)
            pg.screenshot(path=str(OUT / f"01-action-plan-page{tag}.png"), full_page=True)
            sec = pg.locator("#resolved")
            sec.screenshot(path=str(OUT / f"02-recently-resolved{tag}.png"))
            pg.locator("#resolved summary").click()
            pg.wait_for_timeout(200)
            sec.screenshot(path=str(OUT / f"03-recently-resolved-all{tag}.png"))
            pg.goto(app + "/home", wait_until="networkidle")
            pg.wait_for_timeout(300)
            pg.locator('section[aria-label="Today’s action plan"]').first.screenshot(
                path=str(OUT / f"04-home-card{tag}.png")
            )
            assert not errors, errors
            ctx.close()
        _clear(svc, cid)
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = br.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            pg.goto(app + "/action-plan", wait_until="networkidle")
            pg.wait_for_timeout(300)
            pg.locator("#resolved").screenshot(path=str(OUT / f"05-empty{tag}.png"))
            ctx.close()
        br.close()
    print("previews:", OUT)
