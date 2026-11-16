# SPDX-License-Identifier: Apache-2.0
"""Plain words on every screen (non-negotiable 10, owner 16 Nov 2026).

The pages a business leader reads are rendered on the sample company's steady 1 Nov night and
their visible text is checked for data-science terms. A term may appear only where the owner
asked to see the working: a finding's Test, SQL and Run tabs, "How this forecast was made", and
the text behind an (i). Needs `make dev && make seed` (the night world), like the page budget.
"""

from __future__ import annotations

import re
from html import unescape

from othor.engine.sweep_text import LABELS, SHORT
from tests.web.test_brief_web import _brief_id
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import _fp, night  # noqa: F401

TERMS = [
    r"p-values?",
    r"q-values?",
    r"\bq below\b",
    r"\bFDR\b",
    r"Benjamini",
    r"\bPELT\b",
    r"change-?points?",
    r"\bAICc?\b",
    r"\bETS\b",
    r"\bBIC\b",
    r"z-scores?",
    r"standard deviation",
    r"\bregression\b",
    r"\bbimodal\b",
    r"\bGaussian\b",
    r"\bdip test\b",
    r"\bP(10|50|90)\b",
    r"σ",
    r"\bMAE\b",
    r"\bstationar\w*",
    r"\bheteroscedastic\w*",
]
TERM = re.compile("|".join(TERMS), re.I)

# The detectors' names are product names (owner, 16 Nov 2026: "Changepoint" and "Bimodal average"
# stay); they're taken out before the scan, so a term elsewhere is still caught.
NAMES = sorted({*LABELS.values(), *SHORT.values()}, key=len, reverse=True)

# where the working is shown on purpose: removed before the scan
WORKING = [
    r'<span class="pop-body"[^>]*>.*?</span>',  # the text behind an (i)
    r"<details class=\"ahead-depth\">.*?</details>",  # How this forecast was made
]


# Pages the owner reads. The finding panel's Finding tab is plain; its Test tab is the working.
def _pages(bid: str, fp: str) -> list[str]:
    return [
        "/home",
        "/home?panel=questions",
        "/brief",
        f"/brief/{bid}",
        f"/brief/{bid}?view=full",
        f"/brief/{bid}/finding/{fp}",
        "/brief/archive",
        "/sweep",
        "/sweep?tab=watching",
        "/sweep?tab=suppressed",
        f"/sweep/finding/{fp}",
        "/summary",
        "/ontology",
        "/ontology?metric=net_revenue",
        "/sources",
        "/settings",
    ]


def visible_text(html: str) -> str:
    for rx in WORKING:
        html = re.sub(rx, " ", html, flags=re.S)
    html = re.sub(r"<(script|style|template)[^>]*>.*?</\1>", " ", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", unescape(html))
    for name in NAMES:
        text = re.sub(rf"\b{re.escape(name)}\b", " ", text)
    return text


def terms_in(text: str) -> list[str]:
    return [text[max(0, m.start() - 40) : m.end() + 30] for m in TERM.finditer(text)]


def test_the_terms_are_caught_and_the_working_is_left_alone() -> None:
    page = (
        '<p>12 tests tonight · FDR 1%</p><span class="pop-body" role="tooltip">a p-value</span>'
        '<details class="ahead-depth"><summary>How</summary>ETS, P10–P90</details>'
    )
    found = terms_in(visible_text(page))
    assert len(found) == 1 and "FDR" in found[0]


def test_the_pages_an_owner_reads_use_plain_words(night) -> None:  # noqa: F811
    env, h, cid, rid, svc = night
    bid = _brief_id(svc, cid)
    fp = _fp(svc, cid, rid, "net_hiding_gross")
    found: dict[str, list[str]] = {}
    for path in _pages(bid, fp):
        r = env.client.get(h + path)
        assert r.status_code == 200, (path, r.status_code)
        if hits := terms_in(visible_text(r.text)):
            found[path] = hits
    assert found == {}, "data-science words on screen:\n" + "\n".join(
        f"  {p}: {h}" for p, hs in found.items() for h in hs
    )
