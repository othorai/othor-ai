# SPDX-License-Identifier: Apache-2.0
"""The daily 100-night brief check (owner, 19 Nov 2026): run the sample company through ~100 real
nights to 1 Nov 2026 (and the quiet night of 4 Nov), then export the Leadership brief PDF of every
night into one folder for review. Dev only, run by hand; needs `make dev && make seed` (the sample
MySQL and Postgres). It reuses the e2e nights fixture (tests/e2e/test_nights.py), so the nights run
exactly as the e2e test runs them (sources loaded as of each night, the Skarfab answer on 30 Oct,
the Surat answer on 28 Oct, the 5e watches on their dates). No model: the brief's words are the
templates' (the production wording step is a model call).

    OTHOR_E2E_NIGHTS=100 .venv/bin/python -m pytest tests/ui/brief_100_nights.py -s \\
        -p no:cacheprovider -o addopts=""

Writes ~/Desktop/othor-preview/100-nights/<today>/NNN-<night>.pdf and summary.json (per night:
status, title, items, actions, quiet, errors). OTHOR_100_NIGHTS_OUT overrides the folder.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import traceback
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from tests.e2e.test_nights import nights  # noqa: F401  (the module-scoped fixture)

OUT = Path(
    os.environ.get(
        "OTHOR_100_NIGHTS_OUT",
        str(Path.home() / "Desktop" / "othor-preview" / "100-nights" / dt.date.today().isoformat()),
    )
)


def _leadership(s: Any) -> Any:
    from othor.briefs import service

    briefs = service.active(s)
    return next((b for b in briefs if b.is_default), briefs[0] if briefs else None)


def test_export(nights: Any) -> None:  # noqa: F811
    from othor.briefs.models import BriefIssue
    from othor.web import brief_pdf
    from othor.web import brief_view as bvw

    ctx, cid, runs = nights
    OUT.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, Any]] = []
    days = sorted(d for d in runs if d >= min(runs) + dt.timedelta(days=1))
    for n, day in enumerate(days, start=1):
        row: dict[str, Any] = {"n": n, "date": day.isoformat()}
        try:
            with ctx.sf() as s, tenant_scope(cid):
                company = s.scalars(sa.select(Company).where(Company.id == cid)).one()
                brief = _leadership(s)
                issue = s.scalars(
                    sa.select(BriefIssue).where(
                        BriefIssue.brief_id == brief.id, BriefIssue.date == day
                    )
                ).first()
                if issue is None:
                    row["status"] = "no issue"
                    summary.append(row)
                    continue
                body = issue.body or {}
                row.update(
                    status=issue.status,
                    brief=brief.name,
                    title=issue.title,
                    quiet=bool(issue.quiet),
                    items=len(body.get("items") or []),
                    item_titles=[i.get("title") for i in body.get("items") or []],
                    actions=len(body.get("actions") or []),
                    action_texts=[
                        a.get("text") or a.get("template") for a in body.get("actions") or []
                    ],
                )
                bv = bvw.load(s, ctx.settings, brief.id, day, day, issue_id=issue.id)
                data = brief_pdf.build(s, ctx.settings, company, bv, charts=True, appendix=False)
                (OUT / f"{n:03d}-{day.isoformat()}.pdf").write_bytes(data)
        except Exception as e:  # one bad night doesn't stop the others
            row["status"] = "error"
            row["error"] = f"{type(e).__name__}: {e}"
            row["trace"] = traceback.format_exc()[-1500:]
        summary.append(row)
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False, default=str)
    )
    print(f"{len(summary)} nights → {OUT}")
