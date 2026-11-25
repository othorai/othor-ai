# SPDX-License-Identifier: Apache-2.0
"""Audit log (0.35): who did what in Othor AI, and when. The super admin and admins see it (every
member is one of the two). The filters and "Load more" swap rows in place; the CSV is the
filtered rows."""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from othor.audit import view
from othor.core import fmt
from othor.core.tenancy import tenant_scope
from othor.web.context import services
from othor.web.routes.auth import page
from othor.web.routes.pages import Owner, shell_ctx, signed_in

router = APIRouter(include_in_schema=False)


def _view(
    request: Request,
    o: Owner,
    who: str | None,
    kind: str | None,
    days: str | None,
    after: str | None,
) -> dict[str, Any]:
    svc = services(request)
    fmt.set_display_tz(o.company.timezone)
    f = view.Filters.of(who, kind, days)
    with svc.sf() as s, tenant_scope(o.company.id):
        p = view.page(s, f, svc.clock(), after)
        who_options = view.who_options(s)
    return {
        "f": f,
        "rows": p.rows,
        "more": p.more,
        "who_options": who_options,
        "kind_options": view.kind_options(),
        "ranges": view.RANGES,
        "next_only": bool(after),
    }


@router.get("/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    who: str | None = None,
    kind: str | None = None,
    days: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    a = _view(request, o, who, kind, days, None)
    return page(request, "pages/audit.html", {**shell_ctx(o, "audit"), "a": a})


@router.get("/audit/rows", response_class=HTMLResponse)
def audit_rows(
    request: Request,
    who: str | None = None,
    kind: str | None = None,
    days: str | None = None,
    after: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    """The rows, in place: the whole list after a filter (the address follows it), or the next
    page in place of "Load more"."""
    a = _view(request, o, who, kind, days, after)
    r = page(request, "partials/audit_rows.html", {**shell_ctx(o, "audit"), "a": a})
    if not after:
        r.headers["HX-Push-Url"] = f"/audit?{a['f'].query()}"
    return r


@router.get("/audit.csv")
def audit_csv(
    request: Request,
    who: str | None = None,
    kind: str | None = None,
    days: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    """The filtered rows, as the page shows them: when, who, what and kind."""
    svc = services(request)
    fmt.set_display_tz(o.company.timezone)
    f = view.Filters.of(who, kind, days)
    with svc.sf() as s, tenant_scope(o.company.id):
        rows = view.every_row(s, f, svc.clock())
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["When", "Who", "What", "Kind"])
    for r in rows:
        w.writerow([fmt.local(r.at).isoformat(timespec="seconds"), r.who, r.text, r.kind])
    day = fmt.local(svc.clock()).date().isoformat()
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit-log-{day}.csv"'},
    )
