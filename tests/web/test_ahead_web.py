# SPDX-License-Identifier: Apache-2.0
"""v0.19 (M19.4): What's ahead on every surface of the sample night's brief: the app page and the
read-only view after the findings, with "How this forecast was made"; the email (a PNG per row,
the text part's sentences); the PDF after the findings' pages, its depth in the appendix."""

from __future__ import annotations

import re

from othor.briefs.words import shown
from othor.mail.messages import BriefMail, brief_email
from tests.ui.mail.test_emails import CO, DAY, OPEN, ST, UNSUB
from tests.web.test_brief_pdf import _brief_id, _issue, _text
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _shown(body: dict) -> list[dict]:
    return [r for r in body["ahead"] if r.get("says")]


def test_the_brief_page_and_the_view_show_whats_ahead_after_the_findings(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    body, _ = _issue(svc, cid)
    assert _shown(body), "the sample night forecasts its headline numbers"
    bid = _brief_id(env, h)
    for path in (f"/brief/{bid}", f"/brief/{bid}/shared"):
        page = env.client.get(h + path).text
        at = page.index('aria-label="What\'s ahead"')
        assert page.index("brief-items" if "brief-items" in page else "<tbody") < at
        sec = page[at : page.index("</section>", at)]
        for r in _shown(body):
            assert r["name"] in sec and r["confidence"]["word"] in sec
        assert sec.count("How this forecast was made") == len(
            [r for r in body["ahead"] if r.get("depth")]
        )
        assert "<svg" in sec
        if body.get("ahead_note"):
            assert body["ahead_note"] in sec


def test_the_email_has_a_png_per_row_and_the_text_lists_the_sentences(night):  # noqa: F811
    _env, _h, cid, _rid, svc = night
    body, _ = _issue(svc, cid)
    m = BriefMail(
        company_name=CO.name,
        slug=CO.slug,
        timezone=CO.timezone,
        brief_name="Leadership",
        date=DAY,
        body=body,
        words=shown(body, {"source": "template"}),
        email_format="simple",
        run_number=2,
        spec_version=2,
        sources=[],
        owner_email="owner@indigothread.example",
    )
    msg = brief_email(
        m, to="a@indigothread.example", open_url=OPEN, unsubscribe_url=UNSUB, settings=ST
    )
    assert "What’s ahead" in msg.html and "What's ahead" in msg.text
    charted = [r for r in body["ahead"] if r.get("chart")]
    cids = set(re.findall(r'src="cid:([^"]+)"', msg.html))
    assert len({i.cid for i in msg.images} & cids) >= len(charted)
    for r in _shown(body):
        assert f"{r['name']}: {r['says']}" in msg.text


def test_the_pdf_has_whats_ahead_after_the_findings_and_its_appendix(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    body, _ = _issue(svc, cid)
    bid = _brief_id(env, h)
    r = env.client.get(h + f"/brief/{bid}/pdf?charts=1&appendix=1")
    assert r.headers["content-type"] == "application/pdf"
    text, pdf = _text(r.content)
    pages = [p.extract_text() for p in pdf.pages]
    ahead_at = next(i for i, t in enumerate(pages) if "What’s ahead" in t or "What's ahead" in t)
    # each finding's own page (after page 1's list) comes before What's ahead
    own = [
        next(i for i, t in enumerate(pages) if i > 0 and it["title"][:20] in t)
        for it in body["items"]
    ]
    assert own and max(own) < ahead_at
    assert "Appendix · how each forecast was made" in text
    plain = re.sub(r"\s+", " ", text)
    for row in _shown(body):
        assert row["name"] in plain
