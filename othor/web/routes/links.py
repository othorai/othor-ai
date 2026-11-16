# SPDX-License-Identifier: Apache-2.0
"""Recipients' pages, no sign-in (10d–10f), and the Postmark webhook.

- `GET /l/<token>`: the read-only issue the token names (10d); details open read-only. The
  issue comes from the token, never from the URL: another brief, day or company host is a 404.
- An expired link (10e) offers a new one, to the same address only; a removed recipient's link
  says it no longer works.
- `GET /u/<token>`: one click off the brief (10f, decided 6 Nov 2026), with Undo and the other
  briefs this address gets. `POST /u/<token>` is the RFC 8058 one-click from the mail client.
- `POST /hooks/postmark/<secret>`: bounces and spam complaints (the cloud relay).

Every response here is `noindex`, `no-referrer` and `no-store`: a token in a URL must not leak
through a Referer header or a shared cache.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from othor.briefs.models import Brief, BriefVersion, Recipient
from othor.core import fmt
from othor.core.events import record_event
from othor.core.localtime import local_date, local_now
from othor.core.models import Company
from othor.core.tenancy import system_scope, tenant_scope
from othor.mail import delivery as dl
from othor.mail import links, recipients
from othor.mail.models import OPEN, UNSUBSCRIBE, Delivery
from othor.web import brief_view as bvw
from othor.web import sweep_view as sv
from othor.web.context import Site, resolve_site, services
from othor.web.routes.auth import not_found, page
from othor.web.routes.brief import issue_ctx
from othor.web.security import check_csrf

router = APIRouter(include_in_schema=False)
CSRF = [Depends(check_csrf)]
log = logging.getLogger("othor.web.links")
PRIVATE = {
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "private, no-store",
}


def _private(resp: Response) -> Response:
    resp.headers.update(PRIVATE)
    return resp


def _company(request: Request) -> tuple[Site, Company | None]:
    site = resolve_site(request)
    return site, site.company if site.kind == "company" else None


def _missing(request: Request, site: Site) -> Response:
    return _private(not_found(request, site))


def _owner_name(s: Session) -> str:
    who = dl.owner(s)
    return (who[1] or who[0]) if who else "the owner"


def _shell(company: Company) -> dict[str, Any]:
    return {"company": company}


# --- 10d: the brief from the email ------------------------------------------------------------


def _open(request: Request, token: str, fp: str | None, tab: str) -> Response:
    site, company = _company(request)
    if company is None:
        return _missing(request, site)
    svc = services(request)
    now = svc.clock()
    with svc.sf() as s, tenant_scope(company.id):
        r = links.resolve(s, svc.key, company.id, token, OPEN, now)
        if r is None:
            return _missing(request, site)
        if not r.ok:
            return _expired(request, s, company, r, token)
        today = local_date(company, now)
        bv = bvw.load(s, svc.settings, r.brief.id, None, today, issue_id=r.issue.id)
        if bv is None or bv.issue is None:
            return _missing(request, site)
        if r.row.used_at is None:
            r.row.used_at = now
        record_event(
            s,
            actor=recipients.actor(r.recipient),
            action="brief.link_opened",
            object_type="brief_issue",
            object_id=r.issue.id,
            after={"finding": fp} if fp else None,
            at=now,
        )
        s.commit()
        ctx = {**_shell(company), **issue_ctx(s, bv, "simple", svc.settings, readonly=True)}
        base = f"/l/{token}"
        ctx.update(
            base=base,
            pdf={"action": f"{base}/pdf", "hidden": {}},
            q="",
            shared_by=_owner_name(s),
            viewer_line=" · ".join(x for x in (r.recipient.name, r.recipient.email) if x),
            recipient_link={
                "email": r.recipient.email,
                "until": fmt.date_short(local_now(company, r.row.expires_at).date()),
                "unsubscribe": f"{base}/unsubscribe",
            },
        )
        if fp is not None:
            item = next((i for i in bv.body.get("items") or [] if i["fingerprint"] == fp), None)
            rv = sv.load(s, svc.settings, bv.run, today) if bv.run else None
            f = sv.finding(rv, fp) if rv else None
            if item is None or rv is None or f is None:
                return _missing(request, site)
            where = f"{bv.brief.name} brief · {fmt.date_short(bv.issue.date)} · read-only"
            ctx["panel"] = sv.panel(rv, s, f, tab, where=where)
            ctx["run"] = bv.run.number if bv.run else None
        s.expunge_all()
    return _private(page(request, "brief/shared.html", ctx))


@router.get("/l/{token}", response_class=HTMLResponse)
def open_link(request: Request, token: str) -> Response:
    return _open(request, token, None, "finding")


@router.get("/l/{token}/pdf")
def link_pdf(request: Request, token: str, charts: int = 0, appendix: int = 0) -> Response:
    """The brief as a PDF from the recipient's link (M15.7): the link's own checks apply."""
    from othor.web.routes.brief import pdf_response

    site, company = _company(request)
    if company is None:
        return _missing(request, site)
    svc = services(request)
    now = svc.clock()
    with svc.sf() as s, tenant_scope(company.id):
        r = links.resolve(s, svc.key, company.id, token, OPEN, now)
        if r is None or not r.ok:
            return _missing(request, site)
        bv = bvw.load(
            s, svc.settings, r.brief.id, None, local_date(company, now), issue_id=r.issue.id
        )
        if bv is None or bv.issue is None:
            return _missing(request, site)
        resp = pdf_response(s, svc, company, bv, bool(charts), bool(appendix))
        record_event(
            s,
            actor=recipients.actor(r.recipient),
            action="brief.pdf",
            object_type="brief_issue",
            object_id=r.issue.id,
            after={"charts": bool(charts), "appendix": bool(appendix)},
            at=now,
        )
        s.commit()
    return _private(resp)


@router.get("/l/{token}/finding/{fp}", response_class=HTMLResponse)
def open_finding(request: Request, token: str, fp: str, tab: str = "finding") -> Response:
    return _open(request, token, fp, tab)


# --- 10e: expired, a new link to the same address --------------------------------------------


def _expired(
    request: Request,
    s: Session,
    company: Company,
    r: links.Resolved,
    token: str,
    *,
    sent: bool = False,
    refused: str | None = None,
) -> Response:
    svc = services(request)
    now = svc.clock()
    owner = _owner_name(s)
    kicker = f"{r.brief.name} brief · {fmt.date_short(r.issue.date)}"
    email = r.recipient.email
    if r.state == "removed" or r.recipient.status in ("removed", "bounced"):
        ctx = {
            "title": "This link no longer works",
            "lede": f"{email} is no longer on the {r.brief.name} brief.",
            "fine": f"Ask {owner} to add you back.",
            "can_renew": False,
        }
    else:
        reason = refused or links.can_reissue(s, r, local_date(company, now), now)
        today = local_date(company, now)
        got = s.scalars(
            sa.select(Delivery)
            .where(
                Delivery.recipient_id == r.recipient.id,
                Delivery.kind == "brief",
                Delivery.status == "sent",
            )
            .order_by(Delivery.sent_at.desc())
        ).first()
        reached = ""
        if got and got.sent_at and local_date(company, got.sent_at) == today:
            reached = (
                f" Today’s brief reached {email} at "
                f"{fmt.clock(local_now(company, got.sent_at).time())}."
            )
        lede = f"Brief links work for 7 days.{reached}"
        fine = (
            f"The new link goes only to {email}. "
            f"Briefs older than 30 days are not shared by link; ask {owner}."
        )
        if sent:
            lede = f"A new link is on its way to {email}. It works for 7 days."
        elif reason == "limit":
            lede += " You’ve asked for 3 new links today; try again tomorrow."
        elif reason == "too_old":
            lede += " This brief is older than 30 days, so it isn’t shared by link."
        elif reason == "inactive":
            lede += f" {email} is off this brief, so no new link is sent."
        ctx = {
            "title": "This link has expired",
            "lede": lede,
            "fine": fine,
            "can_renew": reason is None and not sent,
        }
    ctx.update(
        _shell(company),
        kicker=kicker,
        brief_name=r.brief.name,
        renew_url=f"/l/{token}/renew",
        viewer_line="",
    )
    s.commit()
    return _private(page(request, "links/expired.html", ctx))


@router.post("/l/{token}/renew", dependencies=CSRF)
def renew(request: Request, token: str) -> Response:
    site, company = _company(request)
    if company is None:
        return _missing(request, site)
    svc = services(request)
    now = svc.clock()
    with svc.sf() as s, tenant_scope(company.id):
        r = links.resolve(s, svc.key, company.id, token, OPEN, now)
        if r is None:
            return _missing(request, site)
        if r.state == "removed":
            return _expired(request, s, company, r, token)
        reason = links.can_reissue(s, r, local_date(company, now), now)
        if reason is not None:
            return _expired(request, s, company, r, token, refused=reason)
        n = s.scalar(
            sa.select(sa.func.count())
            .select_from(Delivery)
            .where(Delivery.kind == "link", Delivery.recipient_id == r.recipient.id)
        )
        mail = dl.brief_mail(s, company, r.brief, r.issue, svc.settings)
        row = dl.send_issue(
            s,
            svc.settings,
            svc.key,
            svc.relay,
            company,
            r.issue,
            r.recipient,
            mail,
            kind="link",
            ref=f"link:{r.issue.id}:{r.recipient.id}:{int(n or 0) + 1}",
            now=now,
        )
        r = links.resolve(s, svc.key, company.id, token, OPEN, now)
        assert r is not None
        return _expired(request, s, company, r, token, sent=row.status == "sent")


@router.post("/l/{token}/unsubscribe", dependencies=CSRF)
def unsubscribe_from_page(request: Request, token: str) -> Response:
    """Unsubscribe from 10d's footer: the open link names the recipient; the page answers as 10f."""
    site, company = _company(request)
    if company is None:
        return _missing(request, site)
    svc = services(request)
    now = svc.clock()
    with svc.sf() as s, tenant_scope(company.id):
        r = links.resolve(s, svc.key, company.id, token, OPEN, now)
        if r is None or r.state == "removed":
            return _missing(request, site)
        utok, _ = links.issue(
            s,
            svc.key,
            company.id,
            recipient_id=r.recipient.id,
            brief_issue_id=r.issue.id,
            purpose=UNSUBSCRIBE,
            now=now,
        )
        recipients.unsubscribe(s, r.recipient, now, via="link_page")
        s.commit()
        return _unsub_page(request, s, company, r, utok, done=True)


# --- 10f: unsubscribe ---------------------------------------------------------------------------


def _schedule(s: Session, brief: Brief, company: Company) -> str:
    v = s.scalars(
        sa.select(BriefVersion).where(
            BriefVersion.brief_id == brief.id, BriefVersion.number == brief.current_version
        )
    ).first()
    body = v.body if v else {}
    from othor.briefs.schedule import PERIODIC, kind, label

    k = kind(body)
    days = k if k not in PERIODIC else k + ", " + label(body).split(" · ", 1)[1]
    return f"{days} {body.get('time') or company.brief_time}"


def _unsub_page(
    request: Request,
    s: Session,
    company: Company,
    r: links.Resolved,
    token: str,
    *,
    done: bool,
) -> Response:
    owner = _owner_name(s)
    email, name = r.recipient.email, r.brief.name
    others: list[tuple[str, str]] = []
    urls: dict[str, str] = {}
    rows = s.execute(
        sa.select(Recipient, Brief)
        .join(Brief, Brief.id == Recipient.brief_id)
        .where(
            Recipient.email == email,
            Recipient.status == "active",
            Recipient.id != r.recipient.id,
            Brief.status == "active",
            Brief.deleted_at.is_(None),
        )
        .order_by(Brief.position, Brief.name)
    ).all()
    for rec, b in rows:
        form = f"other-{rec.id}"
        others.append((f"{b.name} brief · {_schedule(s, b, company)}", form))
        urls[form] = f"/u/{token}/other/{rec.id}"
    if done:
        ctx = {
            "title": f"You won’t get the {name} brief",
            "lede": (
                f"{email} is off the {name} brief from tomorrow. {owner}, who owns it, is told."
            ),
            "pill": "Unsubscribed",
            "action_url": f"/u/{token}/undo",
            "action_label": "Undo",
            "action_primary": False,
        }
    else:
        ctx = {
            "title": f"You’ll keep getting the {name} brief",
            "lede": f"{email} is back on the {name} brief.",
            "pill": None,
            "action_url": f"/u/{token}/again",
            "action_label": "Unsubscribe",
            "action_primary": False,
        }
    ctx.update(_shell(company), brief_name=name, others=others, other_urls=urls, viewer_line="")
    s.commit()
    return _private(page(request, "links/unsubscribe.html", ctx))


@router.get("/u/{token}", response_class=HTMLResponse)
def unsubscribe(request: Request, token: str) -> Response:
    site, company = _company(request)
    if company is None:
        return _missing(request, site)
    svc = services(request)
    now = svc.clock()
    with svc.sf() as s, tenant_scope(company.id):
        r = links.resolve(s, svc.key, company.id, token, UNSUBSCRIBE, now)
        if r is None:
            return _missing(request, site)
        if r.state == "removed" or r.recipient.status == "removed":
            return _expired(request, s, company, r, token)
        if r.state == "expired":
            return _private(
                page(
                    request,
                    "links/expired.html",
                    {
                        **_shell(company),
                        "title": "This link has expired",
                        "lede": "Unsubscribe links work for 60 days.",
                        "fine": f"Use the link in a newer email, or ask {_owner_name(s)}.",
                        "kicker": f"{r.brief.name} brief",
                        "brief_name": r.brief.name,
                        "can_renew": False,
                        "renew_url": "",
                        "viewer_line": "",
                    },
                )
            )
        recipients.unsubscribe(s, r.recipient, now, via="link")
        s.commit()
        return _unsub_page(request, s, company, r, token, done=True)


@router.post("/u/{token}")
async def one_click(request: Request, token: str) -> Response:
    """RFC 8058: the mail client's Unsubscribe button posts `List-Unsubscribe=One-Click`. No
    page, no CSRF (the token is the authority)."""
    site, company = _company(request)
    if company is None:
        return _missing(request, site)
    svc = services(request)
    now = svc.clock()
    with svc.sf() as s, tenant_scope(company.id):
        r = links.resolve(s, svc.key, company.id, token, UNSUBSCRIBE, now)
        if r is None or not r.ok:
            return _private(PlainTextResponse("This link doesn’t work any more.", status_code=404))
        recipients.unsubscribe(s, r.recipient, now, via="one_click")
        s.commit()
    return _private(PlainTextResponse("Unsubscribed."))


@router.post("/u/{token}/{action}", dependencies=CSRF)
def unsubscribe_action(request: Request, token: str, action: str) -> Response:
    """Undo (back on the brief) or unsubscribe again from the undone page."""
    site, company = _company(request)
    if company is None or action not in ("undo", "again"):
        return _missing(request, site)
    svc = services(request)
    now = svc.clock()
    with svc.sf() as s, tenant_scope(company.id):
        r = links.resolve(s, svc.key, company.id, token, UNSUBSCRIBE, now)
        if r is None or not r.ok:
            return _missing(request, site)
        if action == "undo":
            recipients.resubscribe(s, r.recipient, now)
        else:
            recipients.unsubscribe(s, r.recipient, now, via="link")
        s.commit()
        return _unsub_page(request, s, company, r, token, done=action == "again")


@router.post("/u/{token}/other/{rid}", dependencies=CSRF)
def unsubscribe_other(request: Request, token: str, rid: str) -> Response:
    """'Other briefs you get · Unsubscribe' (10f): the same address on another brief."""
    import uuid

    site, company = _company(request)
    if company is None:
        return _missing(request, site)
    svc = services(request)
    now = svc.clock()
    with svc.sf() as s, tenant_scope(company.id):
        r = links.resolve(s, svc.key, company.id, token, UNSUBSCRIBE, now)
        try:
            key = uuid.UUID(rid)
        except ValueError:
            key = None
        other = (
            s.scalars(sa.select(Recipient).where(Recipient.id == key)).first()
            if r is not None and r.ok and key
            else None
        )
        if r is None or other is None or other.email != r.recipient.email:
            return _missing(request, site)
        recipients.unsubscribe(s, other, now, via="link_other")
        s.commit()
        return _unsub_page(request, s, company, r, token, done=r.recipient.status != "active")


# --- the Postmark webhook (bounces, complaints) --------------------------------------------------


@router.post("/hooks/postmark/{secret}")
async def postmark_hook(request: Request, secret: str) -> Response:
    svc = services(request)
    want = svc.settings.postmark_webhook_secret.get_secret_value()
    if not want or not hmac.compare_digest(secret.encode(), want.encode()):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        record = await request.json()
    except ValueError:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if not isinstance(record, dict):
        return JSONResponse({"error": "bad json"}, status_code=400)
    mid = str(record.get("MessageID") or "")
    with svc.sf() as s, system_scope("postmark webhook: which delivery"):
        row = (
            s.scalars(sa.select(Delivery).where(Delivery.provider_id == mid)).first()
            if mid
            else None
        )
        cid = row.company_id if row else None
    if cid is None:
        return JSONResponse({"result": "ignored"})
    now = svc.clock()
    with svc.sf() as s, tenant_scope(cid):
        row = s.scalars(sa.select(Delivery).where(Delivery.provider_id == mid)).one()
        got = recipients.postmark_record(s, row, record, now)
        s.commit()
    log.info("postmark record", extra={"result": got})
    return JSONResponse({"result": got})
