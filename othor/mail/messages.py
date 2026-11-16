# SPDX-License-Identifier: Apache-2.0
"""Email messages built from Jinja2 templates (docs/09: plain HTML, one column, 640 px, inline
CSS, a text part, images inline by cid:). Every number in an email comes from a stored result
(the brief issue, the run, the Ontology); nothing here computes one and no model is called.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from othor.briefs.schedule import since_word
from othor.core import fmt
from othor.core.config import Settings
from othor.mail import images
from othor.mail.relay import InlineImage, Message

env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html"]),
    keep_trailing_newline=True,
    undefined=StrictUndefined,
)
env.globals["fmt"] = fmt

ON_PLAN = ("in band", "on track", "no change")  # as othor.briefs.title.ON_PLAN
STATES = {"measured": "Measured", "assessed": "Assessed", "not_determinable": "Not determinable"}


def sender(settings: Settings, company_name: str | None) -> str:
    """docs/09 (M11b decision A): every company sends from brief@<mail domain>, one verified
    domain, with "<Company> · Othor AI" as the display name; before a company exists,
    "Othor AI <hello@<mail domain>>". The name is quoted as needed, so it can't add an address."""
    from email.headerregistry import Address

    domain = settings.mail_from_domain
    if not company_name:
        return str(Address("Othor AI", "hello", domain))
    return str(Address(f"{company_name} · Othor AI", "brief", domain))


def _render(
    name: str, ctx: dict[str, Any], tz: str | None = None, currency: str | None = None
) -> tuple[str, str]:
    """`tz`: the zone times are shown in (fmt.clock): the company's today, the recipient's from
    v0.15. `currency`: the company's (M15.6), so amounts read ₹ or $."""
    with fmt.display_tz(tz), fmt.money_style(currency):
        html = env.get_template(f"{name}.html").render(**ctx)
        text = env.get_template(f"{name}.txt").render(**ctx)
    return html, text


def sign_in_code(
    *, to: str, code: str, company_name: str, slug: str | None, settings: Settings
) -> Message:
    ctx = {
        "code": fmt.code(code),
        "company_name": company_name,
        "instance": f"{slug}.{settings.base_domain}" if slug else settings.base_domain,
        "logo_cid": images.LOGO_CID,
        "preferences": settings.instance_url(slug, "/settings"),
    }
    html, text = _render("sign_in_code", ctx)
    return Message(
        to=to,
        subject=f"Your Othor AI sign-in code: {fmt.code(code)}",
        html=html,
        text=text,
        sender=sender(settings, company_name if slug else None),
        images=(images.logo(),),
    )


# --- the daily brief (8b, 8b1, 10q) -----------------------------------------------------------


@dataclass(frozen=True)
class BriefMail:
    """What a brief email needs, read from the stored issue and its run (no recomputation)."""

    company_name: str
    slug: str
    timezone: str
    brief_name: str
    date: dt.date
    body: dict[str, Any]
    words: dict[str, str]  # the shown prose: template, or the rewrite that passed the guard
    email_format: str  # simple · full
    run_number: int | None
    spec_version: int | None
    sources: list[str] = field(default_factory=list)  # "erp_db 02:03" · "sales_dw not read"
    owner_email: str | None = None
    # the brief's own weather and outlook, as its run stored them (v0.12 part 1c); None for a
    # run sealed before it
    weather: dict[str, Any] | None = None
    currency: str = "INR"  # the run's (its spec's company settings), so the email matches the brief
    logo: bytes | None = None  # the company's logo (M15.7): the PNG we re-encoded, attached inline


def weather_ctx(w: dict[str, Any] | None, imgs: list[InlineImage]) -> dict[str, Any] | None:
    """The email header (v0.12 part 1c): the scene as an inline picture, the kicker and the line
    as text on the scene's panel colour; the outlook as four rows (icon, week, label, line) and
    the Ahead line. All read from the stored weather; each picture attached once."""
    from othor.briefs import weather

    if not weather.IN_BRIEFS or not w or w.get("scene") not in weather.SCENES:
        return None  # #62: hidden in briefs until v0.21
    s = weather.SCENES[w["scene"]]

    def attach(img: InlineImage) -> str:
        if all(x.cid != img.cid for x in imgs):
            imgs.append(img)
        return img.cid

    head = attach(images.weather_header(s.file))  # the header first, then the icons
    o = w.get("outlook") or {}
    weeks = [
        {
            "name": k["name"],
            "dates": k["dates"],
            "label": k["label"],
            "line": k["line"],
            "cid": attach(images.weather_icon(k["file"])),
            "worst": o.get("worst") == k["week"],
        }
        for k in o.get("weeks") or []
        if k.get("file")
    ]
    return {
        "cid": head,
        "width": images.HEADER[0],
        "height": images.HEADER[1],
        "icon": images.ICON,
        "alt": weather.alt(s.file),
        "kicker": f"Today · {s.label}".upper(),
        "line": w["line"],
        "panel": s.panel,
        "text": s.text,
        "weeks": weeks,
        "title": f"Ahead · next {fmt.num(len(weeks))} weeks" if weeks else "",
        "ahead": o.get("ahead_line") if weeks else None,
    }


def tz_label(tz: str, d: dt.date, at: str) -> str:
    hh, mm = (int(x) for x in at.split(":"))
    return dt.datetime.combine(d, dt.time(hh, mm), tzinfo=ZoneInfo(tz)).tzname() or tz


def since_text(since: dict[str, int]) -> str:
    """'2 new · 1 worse · 1 open · 1 resolved' (8b1), only the counts that aren't zero."""
    parts = [
        f"{fmt.num(since[k])} {k}"
        for k in ("new", "worse", "better", "open", "resolved")
        if since.get(k)
    ]
    return " · ".join(parts)


def stale_text(body: dict[str, Any]) -> str:
    tiles = [t for t in body.get("tiles") or [] if t.get("stale")]
    if not tiles:
        n = len(body.get("stale") or [])
        return f"{fmt.num(n)} number{'s' if n != 1 else ''} from a stale source" if n else ""
    named = ", ".join(f"{t['label']} {t['stale']}" for t in tiles)
    n = len(tiles)
    return f"{fmt.num(n)} number{'s' if n != 1 else ''} from a stale source: {named}"


def footer_text(m: BriefMail) -> str:
    out = "Every number is computed from your sources"
    if m.run_number is not None:
        out += f" · run #{m.run_number:04d}"
    if m.spec_version:
        out += f" · spec v{m.spec_version}"
    return out + (" · replayable" if m.run_number is not None else "")


# M15.7d (owner, 14 Nov 2026): what changed leads the subject; the date ends it, so each morning
# is its own email (Gmail threads equal subjects)
CHANGED = (("new", "New"), ("worse", "Worse"), ("came back", "Came back"))


def _changed(item: dict[str, Any]) -> str | None:
    tag = str(item.get("change") or "")
    return next((w for w, t in CHANGED if tag.startswith(t)), None)


def brief_subject(m: BriefMail) -> str:
    """The highest-ranked item that changed (New, Worse, Came back: tonight's tags, or a Weekly or
    Monthly issue's against its last issue), else "Still open · {top item}"; then the brief's name
    and the short date. A quiet day says so."""
    b = m.body
    when = fmt.date_short(m.date)
    if b.get("quiet"):
        tiles = b.get("tiles") or []
        plan = bool(tiles) and all(
            t["comparison"]["text"].startswith(ON_PLAN) and not t.get("stale") for t in tiles
        )
        return f"Quiet day{' · on plan' if plan else ''} · {m.brief_name} · {when}"
    items = b.get("items") or []
    lead = next((i for i in items if _changed(i)), None)
    if lead is not None:
        head = str(lead["title"])
    elif items:
        head = f"Still open · {items[0]['title']}"
    else:
        head = m.words.get("title", "")
    return f"{head} · {m.brief_name} · {when}"


def brief_preheader(m: BriefMail) -> str:
    b = m.body
    ask = b.get("ask") or {}
    who = str(ask.get("to") or "").split(" ")[0]
    if b.get("quiet"):
        tiles = b.get("tiles") or []
        ok = sum(1 for t in tiles if t["comparison"]["text"].startswith(ON_PLAN))
        tail = f" · {fmt.num(ok)} of {fmt.num(len(tiles))} numbers on plan" if tiles else ""
        return "Nothing needs you today" + tail
    items = b.get("items") or []
    n = len(items)
    noun = "thing needs" if n == 1 else "things need"
    if m.email_format == "full":
        noun = "item" if n == 1 else "items"
        head = f"{fmt.num(n)} {noun}"
    else:
        head = f"{fmt.num(n)} {noun} you"
    tags = [_changed(i) for i in items]
    moved = [f"{fmt.num(tags.count(w))} {w}" for w, _t in CHANGED if tags.count(w)]
    return " · ".join([*moved, head]) + (f" · ask {who} one question today" if who else "")


def brief_email(
    m: BriefMail, *, to: str, open_url: str, unsubscribe_url: str, settings: Settings
) -> Message:
    b = m.body
    kind = "quiet" if b.get("quiet") else ("full" if m.email_format == "full" else "simple")
    imgs: list[InlineImage] = [images.logo()]
    items = []
    for i in b.get("items") or []:
        img = images.chart(i.get("chart")) if kind == "simple" else None
        if img is not None and all(x.cid != img.cid for x in imgs):
            imgs.append(img.image)
        state = STATES.get(i["state"], i["state"])
        if i["state"] == "assessed" and i.get("confidence"):
            state += f" · {str(i['confidence']).capitalize()}"
        items.append({**i, "img": img, "state_text": state})
    from othor.briefs.tiles import emphasis

    # the tile colour rule (M15.3): red only for a clear move, as on the brief page
    tiles = [
        {
            **t,
            "on_plan": t["comparison"]["text"].startswith(ON_PLAN),
            "role": {"clear": "negative", "edge": "neutral", "in": "muted"}.get(
                emphasis(t), t.get("role")
            ),
        }
        for t in b.get("tiles") or []
    ]
    # v0.19 (M19.4): What's ahead, each row's chart as a PNG
    ahead = []
    for r in b.get("ahead") or []:
        big = images.AHEAD_BESIDE_CARD if r.get("card") else None
        img = images.chart(r.get("chart"), ahead=True, size=big) if r.get("chart") else None
        # the forecast card's range bar (owner, 16 Nov 2026), as Big Picture's email draws it
        rng = None
        if r.get("card"):
            from othor.briefs.ahead import range_chart as range_spec

            rng = images.spec_png(range_spec(r["card"]), (236, 56))
        for x in (img, rng):
            if x is not None and all(y.cid != x.cid for y in imgs):
                imgs.append(x.image)
        ahead.append({**r, "img": img, "range_img": rng})
    wx = weather_ctx(m.weather, imgs)
    subject = brief_subject(m)
    ctx = {
        "subject": subject,
        "preheader": brief_preheader(m),
        "company_name": m.company_name,
        "logo_cid": images.LOGO_CID,
        "brief_name": m.brief_name,
        "date_label": fmt.date_short(m.date),
        "date_long": f"{fmt.date_short(m.date)} {m.date.year}",
        "tz_label": tz_label(m.timezone, m.date, str(b.get("time") or "06:00")),
        "b": b,
        "words": m.words,
        "items": items,
        "ahead": ahead,
        "ahead_note": b.get("ahead_note"),
        "tiles": tiles,
        "since": since_text(b.get("since") or {}),
        "since_word": since_word(b),
        "sources": m.sources,
        "stale_line": stale_text(b),
        "open_url": open_url,
        "unsubscribe_url": unsubscribe_url,
        "footer": footer_text(m),
        "wx": wx,
        "co_logo": images.company_logo(m.logo, imgs),
    }
    html, text = _render(f"brief_{kind}", ctx, m.timezone, m.currency)
    return Message(
        to=to,
        subject=subject,
        html=html,
        text=text,
        sender=sender(settings, m.company_name),
        reply_to=m.owner_email,
        headers={
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
        images=tuple(imgs),
    )


def close_code(*, to: str, code: str, company_name: str, slug: str, settings: Settings) -> Message:
    """M18.2: the code that confirms closing a company (purpose "close")."""
    subject = f"Your Othor AI code to delete {company_name}: {fmt.code(code)}"
    ctx = {
        "code": fmt.code(code),
        "company_name": company_name,
        "subject": subject,
        "preheader": "Expires in 10 minutes. Didn’t ask to delete it? Don’t share this code.",
        "instance": f"{slug}.{settings.base_domain}",
        "logo_cid": images.LOGO_CID,
        "preferences": settings.instance_url(slug, "/settings"),
        "support_email": settings.support_email,  # shown only when set (M20.2)
    }
    html, text = _render("close_code", ctx)
    return Message(
        to=to,
        subject=subject,
        html=html,
        text=text,
        sender=sender(settings, company_name),
        images=(images.logo(),),
    )


def company_closed(
    *, to: str, company_name: str, slug: str, purge_on: str, settings: Settings
) -> Message:
    """M18.2: to every member once the company is closed (the host no longer resolves)."""
    return owner_email(
        "company_closed",
        to=to,
        subject=f"[Othor AI] {company_name} is deleted",
        preheader=f"Runs and briefs have stopped. The data is removed for good on {purge_on}.",
        company_name=company_name,
        slug=slug,
        settings=settings,
        kicker="Company deleted",
        lead=(
            f"{company_name} is deleted. Nightly runs and briefs have stopped, everyone has been "
            "signed out, and the sources' passwords and Google sign-ins are removed."
        ),
        after=(f"Everything else is removed for good on {purge_on}. {_restore_line(settings)}"),
    )


def _restore_line(settings: Settings) -> str:
    """Who restores a deleted company: the install's support address (othor.ai sets its own),
    or the person who runs a self-hosted install (M20.5)."""
    if settings.support_email:
        return f"To restore it before then, write to {settings.support_email}."
    return "To restore it before then, ask whoever runs this Othor AI."


def purge_reminder(
    *, to: str, company_name: str, slug: str, purge_on: str, settings: Settings
) -> Message:
    """M18.3: 7 days before a deleted company's data is removed for good."""
    return owner_email(
        "purge_reminder",
        to=to,
        subject=f"[Othor AI] {company_name} is removed for good on {purge_on}",
        preheader=_restore_line(settings),
        company_name=company_name,
        slug=slug,
        settings=settings,
        kicker="Removed for good in 7 days",
        lead=(
            f"{company_name} was deleted. On {purge_on} its data is removed for good: the "
            "Ontology, briefs, findings, history and every file. This can’t be undone."
        ),
        after=_restore_line(settings),
    )


# --- owner emails (8c, 8d, 8e) ----------------------------------------------------------------


def owner_email(
    name: str,
    *,
    to: str,
    subject: str,
    preheader: str,
    company_name: str,
    slug: str,
    settings: Settings,
    **ctx: Any,
) -> Message:
    """An email to the owner: the owner footer ("Sent by your Othor AI instance at …")."""
    full = {
        **ctx,
        "subject": subject,
        "preheader": preheader,
        "company_name": company_name,
        "logo_cid": images.LOGO_CID,
        "instance": f"{slug}.{settings.base_domain}",
        "preferences": settings.instance_url(slug, "/settings"),
    }
    html, text = _render(name, full)
    return Message(
        to=to,
        subject=subject,
        html=html,
        text=text,
        sender=sender(settings, company_name),
        images=(images.logo(),),
    )


# --- a request for access (M11d part C) -------------------------------------------------------


def access_request(
    *,
    to: str,
    name: str,
    email: str,
    company: str,
    message: str | None,
    settings: Settings,
    team_size: str | None = None,
    runs_today: str | None = None,
) -> Message:
    """To Othor AI's support address: who asked, from which company, and what they said.
    Reply-To is the requester, so answering goes straight back to them."""
    from html import escape

    lines = [
        f"Name: {name}",
        f"Email: {email}",
        f"Company: {company}",
        f"People who work there: {team_size or '(not given)'}",
        f"What they run today: {runs_today or '(not given)'}",
        "",
        message or "(nothing else)",
    ]
    text = "A request for access to Othor AI.\n\n" + "\n".join(lines) + "\n"
    html = "<p>A request for access to Othor AI.</p>" + "".join(
        f"<p>{escape(line)}</p>" for line in lines if line
    )
    return Message(
        to=to,
        subject=f"Request access: {company}",
        html=html,
        text=text,
        sender=sender(settings, None),
        reply_to=email,
    )


def connector_request(
    *,
    to: str,
    company: str,
    requester: str,
    system: str,
    version: str | None,
    wants: str | None,
    reach: str | None,
    settings: Settings,
) -> Message:
    """To Othor AI's support address (M15.4): which company asked for which connector, and how
    they reach it today. Reply-To is the owner who asked."""
    from html import escape

    lines = [
        f"Company: {company}",
        f"Asked by: {requester}",
        f"System: {system}",
        f"Version: {version or '(not given)'}",
        f"How they reach it today: {reach or '(not given)'}",
        "",
        f"What they want from it: {wants or '(not given)'}",
    ]
    text = "A request for a connector.\n\n" + "\n".join(lines) + "\n"
    html = "<p>A request for a connector.</p>" + "".join(
        f"<p>{escape(line)}</p>" for line in lines if line
    )
    return Message(
        to=to,
        subject=f"Connector request: {system} · {company}",
        html=html,
        text=text,
        sender=sender(settings, None),
        reply_to=requester,
    )


def big_picture_email(
    r: dict[str, Any], *, to: str, company_name: str, slug: str, settings: Settings
) -> Message:
    """v0.19 (M19.5): the stored Big Picture report, sent to the owner who asked, in the panel's
    layout (owner, 15 Nov 2026): the summary, the tiles, a card per finding with its chart, the
    money at stake, the actions, the forecasts with their range bars. No model call."""
    from othor.briefs.big_picture import plain

    imgs = [images.logo()]

    def add(img: images.EmailChart) -> images.EmailChart:
        if all(i.cid != img.cid for i in imgs):
            imgs.append(img.image)
        return img

    charts = {
        c["fact"]: add(images.spec_png(c["spec"], (552, 166), legend=True))
        for c in r.get("findings") or []
        if c.get("spec") is not None
    }
    ranges = [add(images.spec_png(c["range"], (300, 64))) for c in r.get("forecasts") or []]
    one = plain(r.get("one_line"))
    subject = f"Big Picture · {company_name}"
    base = settings.instance_url(slug, "")
    ctx = {
        "r": r,
        "plain": plain,
        "charts": charts,
        "ranges": ranges,
        "subject": subject,
        "preheader": one[:140],
        "company_name": company_name,
        "logo_cid": images.LOGO_CID,
        "instance": f"{slug}.{settings.base_domain}",
        "preferences": settings.instance_url(slug, "/settings"),
        "open_url": settings.instance_url(slug, "/big-picture"),
        "base": base,
    }
    html, text = _render("big_picture", ctx)
    return Message(
        to=to,
        subject=subject,
        html=html,
        text=text,
        sender=sender(settings, company_name),
        images=tuple(imgs),
    )
