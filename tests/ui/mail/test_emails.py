# SPDX-License-Identifier: Apache-2.0
"""Every email as a snapshot (M9 acceptance: "emails render in Gmail/Outlook, snapshot tests of
the HTML"), from the real 1 Nov issue of the sample company (tests/fixtures/mail/), plus:

- a lint for what Gmail and Outlook need: tables at 640 px with an MSO wrapper, inline CSS only
  (no <style>, no external CSS or web fonts), no SVG, scripts, forms or data: URIs, every image
  by cid: with alt, width and height, every link absolute, a text part;
- no number in a brief email that isn't in the stored issue (or its run line).

Regenerate on purpose: OTHOR_UPDATE_GOLDEN=1 uv run pytest tests/ui/mail
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

import pytest

from othor.briefs.words import shown
from othor.core.config import Settings
from othor.core.models import Company
from othor.mail import alerts
from othor.mail.messages import (
    BriefMail,
    brief_email,
    close_code,
    company_closed,
    purge_reminder,
    sign_in_code,
)
from othor.mail.relay import Message
from tests.unit.mail.world import issue_1nov, quiet_from, weather_1nov

GOLDEN = Path(__file__).parent / "golden"
ST = Settings(
    _env_file=None,
    base_domain="othor.ai",
    mail_from_domain="othor.ai",
    support_email="support@othor.ai",
)  # type: ignore[call-arg]
CO = Company(
    slug="indigothread", name="Indigo Thread Co.", timezone="Asia/Kolkata", brief_time="06:00"
)
DAY = dt.date(2026, 11, 1)
OPEN = "https://indigothread.othor.ai/l/OPEN-TOKEN"
UNSUB = "https://indigothread.othor.ai/u/UNSUB-TOKEN"


WX = weather_1nov()  # the brief's own weather that night (v0.12 part 1c)


def _brief(body, fmt="simple", wx=WX) -> Message:
    m = BriefMail(
        company_name=CO.name,
        slug=CO.slug,
        timezone=CO.timezone,
        brief_name="Leadership",
        date=DAY,
        body=body,
        words=shown(body, {"source": "template"}),
        email_format=fmt,
        run_number=2,
        spec_version=2,
        sources=["erp_db 02:03", "sales_dw 02:04"],
        owner_email="uk@indigothread.example",
        weather=wx,
    )
    return brief_email(
        m, to="priya@indigothread.example", open_url=OPEN, unsubscribe_url=UNSUB, settings=ST
    )


def _stale_body():
    b = issue_1nov()
    b["stale"] = ["net_revenue"]
    b["tiles"][0]["stale"] = "as of Fri"
    return b


def emails() -> dict[str, Message]:
    at = dt.datetime(2026, 10, 31, 20, 33, tzinfo=dt.UTC)
    return {
        "8a_sign_in_code": sign_in_code(
            to="uk@indigothread.example",
            code="482913",
            company_name=CO.name,
            slug=CO.slug,
            settings=ST,
        ),
        "8b_brief_simple": _brief(issue_1nov()),
        "8b_brief_simple_stale": _brief(_stale_body()),
        "8b1_brief_full": _brief(issue_1nov(), "full"),
        "10q_brief_quiet": _brief(quiet_from(issue_1nov())),
        "8c_run_failed": alerts.run_failed(
            ST,
            CO,
            alerts.FailedRun(
                number=28,
                run_date=dt.date(2026, 10, 29),
                ended_at=dt.datetime(2026, 10, 28, 20, 33, tzinfo=dt.UTC),
                step="read:sales_dw",
                error="OperationalError",
                source_id="3f2a",
                others_ok=True,
            ),
            to="uk@indigothread.example",
            brief_time="06:00",
        ),
        "18_close_code": close_code(
            to="uk@indigothread.example",
            code="482913",
            company_name=CO.name,
            slug=CO.slug,
            settings=ST,
        ),
        "18_company_closed": company_closed(
            to="uk@indigothread.example",
            company_name=CO.name,
            slug=CO.slug,
            purge_on="15 Dec 2026",
            settings=ST,
        ),
        "18_purge_reminder": purge_reminder(
            to="uk@indigothread.example",
            company_name=CO.name,
            slug=CO.slug,
            purge_on="15 Dec 2026",
            settings=ST,
        ),
        "18_export_ready": alerts.export_ready(
            ST,
            CO,
            "7d1c0e7e-0000-4000-8000-000000000001",
            dt.datetime(2026, 11, 3, 3, 0, tzinfo=dt.UTC),
            to="uk@indigothread.example",
        ),
        "8c_source_stale": alerts.source_stale(
            ST,
            CO,
            to="uk@indigothread.example",
            source="sales_dw",
            source_id="3f2a",
            reason="OperationalError",
            run_number=2,
            run_date=DAY,
            ended_at=at,
            brief_time="06:00",
        ),
        "8c_run_late": alerts.run_late(
            ST,
            CO,
            to="uk@indigothread.example",
            run_number=2,
            run_date=DAY,
            now=dt.datetime(2026, 11, 1, 4, 30, tzinfo=dt.UTC),
            brief_time="06:00",
        ),
        "8d_ontology_waiting": alerts.ontology_waiting(
            ST,
            CO,
            to="uk@indigothread.example",
            rows=[
                {"name": "Net revenue", "note": "needs confirmation"},
                {"name": "DSO", "note": "needs confirmation"},
                {"name": "Gross margin per metre", "note": "two answers to pick from"},
            ],
            total=3,
            drafted_on=dt.date(2026, 10, 21),
        ),
        "8e_weekly_digest": alerts.weekly_digest(
            ST,
            CO,
            to="uk@indigothread.example",
            first=dt.date(2026, 10, 26),
            last=DAY,
            stats={"tests": 1484, "fired": 12, "in_brief": 4, "open": 8},
            open_items=[("South fell ₹1.4 Cr; West’s rise hid it", "Priya Menon · day 1")],
            not_in_brief=[("Gross margin by plant · Kochi", "₹0.9 Cr")],
        ),
    }


EMAILS = emails()


def _with_weather() -> dict:
    """The brief emails with the weather switched on (hidden by default since #62, 14 Nov 2026;
    kept tested for v0.21's revisit)."""
    from othor.briefs import weather

    was = weather.IN_BRIEFS
    weather.IN_BRIEFS = True
    try:
        return emails()
    finally:
        weather.IN_BRIEFS = was


EMAILS_WX = _with_weather()


@pytest.mark.parametrize("name", sorted(EMAILS))
def test_golden(name):
    msg = EMAILS[name]
    for ext, got in (("html", msg.html), ("txt", f"Subject: {msg.subject}\n\n{msg.text}")):
        path = GOLDEN / f"{name}.{ext}"
        if os.environ.get("OTHOR_UPDATE_GOLDEN") == "1" or not path.exists():
            path.write_text(got)
        assert got == path.read_text(), (
            f"{name}.{ext} changed; review it, then OTHOR_UPDATE_GOLDEN=1"
        )


@pytest.mark.parametrize("name", sorted(EMAILS))
def test_renders_in_gmail_and_outlook(name):
    msg = EMAILS[name]
    h = msg.html
    low = h.lower()
    assert 'width="640"' in h and "max-width:640px" in h  # one column, 640 px
    assert "<!--[if mso]>" in h and '<meta name="color-scheme" content="light only">' in h
    for bad in ("<svg", "<script", "<form", "<style", "<link ", "@import", "data:", "<iframe"):
        assert bad not in low, (name, bad)
    assert "class=" not in h  # inline CSS only: no stylesheet to strip
    imgs = re.findall(r"<img [^>]*>", h)
    assert imgs, name  # the logo at least
    cids = {i.cid for i in msg.images}
    for tag in imgs:
        src = re.search(r'src="cid:([^"]+)"', tag)
        assert src and src.group(1) in cids, tag
        assert re.search(r'\balt="', tag) and 'width="' in tag and 'height="' in tag, tag
    assert {re.search(r'src="cid:([^"]+)"', t).group(1) for t in imgs} == cids  # none unused
    for href in re.findall(r'href="([^"]*)"', h):
        assert href.startswith("https://"), (name, href)
    assert msg.text.strip() and "{{" not in msg.text and "{{" not in h
    # brand name always "Othor AI": "<Company> · Othor AI" from brief@ (M11b), or "Othor AI"
    from email.utils import parseaddr

    shown, addr = parseaddr(msg.sender)
    assert shown == "Othor AI" or shown.endswith(" · Othor AI"), msg.sender
    assert addr.split("@")[0] in ("brief", "hello"), msg.sender


NUM = re.compile(r"(?<![\w#])[+−-]?\d[\d,]*(?:\.\d+)?")


def _numerals(text: str) -> set[str]:
    text = re.sub(r"https?://\S+", " ", text)
    return {n.lstrip("+−-").replace(",", "") for n in NUM.findall(text)}


@pytest.mark.parametrize("name", ["8b_brief_simple", "8b1_brief_full", "10q_brief_quiet"])
def test_every_number_comes_from_the_issue(name):
    """Non-negotiable 1: the email repeats computed values; nothing is written in."""
    body = quiet_from(issue_1nov()) if "quiet" in name else issue_1nov()
    allowed = _numerals(json.dumps(body, ensure_ascii=False))
    allowed |= _numerals(json.dumps(WX, ensure_ascii=False))  # the stored weather (part 1c)
    allowed |= {
        "0002",
        "2",
        "1",
        "2026",
        "06",
        "6",  # the hour of 6:00 AM (12-hour times, 16 Nov 2026)
        "00",
        "02",
        "03",
        "04",
        "4",
    }  # run, spec, date, time
    got = _numerals(EMAILS[name].text) | _numerals(EMAILS[name].subject)
    assert got <= allowed, got - allowed


def test_alt_text_carries_the_numbers():
    msg = EMAILS["8b_brief_simple"]
    alts = re.findall(r'<img [^>]*alt="([^"]*)"', msg.html)
    assert any("South −₹1.40 Cr, West +₹1.21 Cr" in a for a in alts)


def test_item_charts_are_pngs_and_deterministic():
    a, b = _brief(issue_1nov()), _brief(issue_1nov())
    pngs = [i for i in a.images if i.cid.startswith("chart-")]
    # P1, P7, P6 (missing records has no chart by design), then What's ahead's 5 (M19.4)
    # What's ahead: each row's line, and its forecast card's range bar (16 Nov 2026)
    ahead = issue_1nov()["ahead"]
    lines = len([r for r in ahead if r.get("chart")])
    ranges = len([r for r in ahead if r.get("card")])
    assert len(pngs) == 3 + lines + ranges == 13
    assert all(i.data.startswith(b"\x89PNG") for i in pngs)
    assert [i.data for i in a.images] == [i.data for i in b.images]


def test_subjects_and_preheaders():
    assert (
        EMAILS["8b_brief_simple"].subject
        == "South fell ₹1.4 Cr; West’s rise hid it · Leadership · Sun 1 Nov"
    )  # M15.7d: what changed leads (every item is new on the first night); the date ends it
    assert "4 new · 4 things need you · ask Priya one question today" in (
        EMAILS["8b_brief_simple"].html
    )
    assert EMAILS["8b1_brief_full"].subject.endswith("· Leadership · Sun 1 Nov")
    assert EMAILS["10q_brief_quiet"].subject == "Quiet day · Leadership · Sun 1 Nov"
    assert "1 number from a stale source: Net revenue · Oct as of Fri" in (
        EMAILS["8b_brief_simple_stale"].text
    )


# --- business weather (v0.12 part 1c) -----------------------------------------------------------

BRIEFS = ["8b_brief_simple", "8b1_brief_full", "10q_brief_quiet"]


@pytest.mark.parametrize("name", BRIEFS)
def test_the_header_is_the_briefs_scene_with_its_line_on_the_panel(name):
    from othor.briefs import weather

    msg = EMAILS_WX[name]
    h = msg.html
    s = weather.SCENES[WX["scene"]]
    cid = f"wx-{s.file}@othor.ai"
    # the scene as an inline picture (1200 x 240, shown at 600 x 120) with "<Label>: <place>"
    tag = re.search(rf'<img src="cid:{re.escape(cid)}"[^>]*>', h).group(0)
    assert 'width="600" height="120"' in tag
    assert f'alt="{weather.alt(s.file)}"' in tag.replace("&#39;", "’")
    img = next(i for i in msg.images if i.cid == cid)
    assert img.data.startswith(b"\x89PNG") and img.data[16:24] == (1200).to_bytes(4, "big") + (
        240
    ).to_bytes(4, "big")
    assert len(img.data) < 100_000  # the header under 100 KB
    # the kicker and the line as text under it, white on the scene's panel colour (13 Nov 2026)
    cell = h[h.index(f'bgcolor="{s.panel}" style="padding:') :]
    assert f"TODAY · {s.label.upper()}" in cell[:900] and WX["line"] in cell[:900]
    assert f"color:{s.text}" in cell[:900]
    # above the brief's own title
    assert h.index(cid) < h.index("<h1")
    # the outlook: four rows, each an icon (96 x 96 PNG) with alt text, the week, label and line
    o = WX["outlook"]
    for k in o["weeks"]:
        icon = f"wx-icon-{k['file']}@othor.ai"
        assert f'<img src="cid:{icon}" width="32" height="32" alt="{k["label"]}"' in h
        assert k["name"] in h and k["dates"] in h and k["line"] in h
    assert f'<b style="color:#1F2A37">Ahead:</b> {o["ahead_line"]}' in h.replace("&#39;", "’")
    # each picture attached once, even when two weeks share a scene
    cids = [i.cid for i in msg.images]
    assert len(cids) == len(set(cids))
    assert len({k["file"] for k in o["weeks"]}) < len(o["weeks"])  # the fixture repeats one


@pytest.mark.parametrize("name", BRIEFS)
def test_the_plain_text_has_the_kicker_the_line_and_the_ahead_line_only(name):
    from othor.briefs import weather

    text = EMAILS_WX[name].text
    s = weather.SCENES[WX["scene"]]
    head = f"TODAY · {s.label.upper()}\n{WX['line']}\nAhead: {WX['outlook']['ahead_line']}\n\n"
    assert text.startswith(head)
    for k in WX["outlook"]["weeks"]:
        assert k["dates"] not in text  # the weeks are in the HTML only


def test_without_stored_weather_the_email_has_no_header():
    msg = _brief(issue_1nov(), wx=None)
    assert "wx-" not in msg.html and "TODAY ·" not in msg.text
    assert [i.cid for i in msg.images if i.cid.startswith("wx-")] == []
    assert msg.html.count("<tr>") < EMAILS_WX["8b_brief_simple"].html.count("<tr>")


def test_email_size_with_the_header():
    """The HTML and its inline pictures: the header stays under 100 KB; the total is noted."""
    for name in BRIEFS:
        msg = EMAILS[name]
        wx = sum(len(i.data) for i in msg.images if i.cid.startswith("wx-"))
        total = (
            len(msg.html.encode()) + len(msg.text.encode()) + sum(len(i.data) for i in msg.images)
        )
        assert wx < 100_000, (name, wx)
        assert total < 400_000, (name, total)


@pytest.mark.parametrize("name", BRIEFS)
def test_brief_emails_carry_no_weather_until_v0_21(name):
    """#62 (owner, 14 Nov 2026): the weather is hidden in briefs, emails included."""
    msg = EMAILS[name]
    assert "wx-" not in msg.html and "TODAY · " not in msg.text
    assert not [i for i in msg.images if i.cid.startswith("wx-")]
