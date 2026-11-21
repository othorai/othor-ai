# SPDX-License-Identifier: Apache-2.0
"""Business Model 2.4 on the page (L1, L2, L4): the level card and its checklist at the top of the
Business Model page, each missing item's sentence pre-filled in the one chat's box, the example
PDF, and the 20 Nov rebates line becoming one Apply in both boxes (Change Net revenue and Update
your Business Model). The model is a fake; values before and after are computed."""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import quote

import pytest

from othor.core.tenancy import tenant_scope
from othor.ontology import service
from tests.fixtures.sample_company.ontology import T, m, op
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

LINE = "Net revenue is invoiced value minus rebates (erp_db cost_ledger account 4410)"


class _Got:
    def __init__(self, output: Any) -> None:
        self.output, self.guard = output, "passed"

    @property
    def ok(self) -> bool:
        return True


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    said: dict[str, Any] = {}

    class GW:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def complete(self, company_id: Any, prompt: str, version: str, inputs: Any, **k: Any):
            said["inputs"], said["prompt"] = inputs, (prompt, version)
            return _Got(said["out"])

    import othor.ai.gateway as g

    monkeypatch.setattr(g, "ModelGateway", GW)
    return said


def _page(env: Any, url: str) -> str:
    return html.unescape(env.client.get(url).text)


def test_the_level_card_and_checklist(night) -> None:  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):  # as the demo accounts set up through onboarding
        gone = [service.retire("owner", r.key) for r in service.active(s, ["owner"])]
        service.apply(s, gone, actor="test", reason="No owners")
        s.commit()
    raw = env.client.get(h + "/ontology").text
    page = html.unescape(raw)
    card = page.split('aria-label="Business Model level"', 1)[1].split("</section>", 1)[0]
    assert "<b>Level 1 of 3 to reach</b>" in card and "left to reach full potential" in card
    for g in ("1 · Must have", "2 · Good to have", "3 · Full potential"):
        assert g in card
    # the missing owners: what they unlock and the sentence to say
    assert "Owners for the main areas" in card and "Each question goes to a named person" in card
    assert "“The Head of Sales owns Net revenue" in card
    say = re.search(r'href="(/ontology\?panel=othor&amp;subject=ontology&amp;say=The[^"]+)"', raw)
    assert say, card[-2000:]
    # the sentence opens the one chat with it in the box, for the owner to finish and send
    chat = _page(env, h + html.unescape(say[1]))
    box = chat.split('id="oc-reply"', 1)[1].split("</textarea>", 1)[0]
    assert "The Head of Sales owns Net revenue" in box
    # no draft note on the page itself; the level card is code's count, not a draft
    assert "Drafts only" not in card
    # L4: the example, definitions only
    assert 'href="/ontology/example.pdf"' in card
    r = env.client.get(h + "/ontology/example.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert (
        r.headers["content-type"] == "application/pdf"
        and "attachment" in r.headers["content-disposition"]
    )


def _without_rebates(svc: Any, cid: Any) -> None:
    """As the demo accounts: net revenue = invoiced value − credit notes, no Rebates number."""
    with svc.sf() as s, tenant_scope(cid):
        nr = next(r for r in service.active(s, ["metric"]) if r.key == "net_revenue")
        body = dict(nr.body)
        body["formula"] = op("-", m("invoiced_value", "zero"), m("credit_note_value", "zero"))
        body["breakdown_formulas"] = {
            "plant": op("-", m("line_revenue", "zero"), m("credit_note_value", "zero"))
        }
        service.apply(
            s,
            [
                service.retire("rule", "alloc_rebates"),
                service.retire("metric", "rebates"),
                service.put("metric", "net_revenue", body),
            ],
            actor="test",
            reason="As the demo accounts",
        )
        s.commit()


@pytest.mark.parametrize("subject", ["ontology:metric:net_revenue", "ontology"])
def test_the_rebates_line_is_one_apply_in_both_boxes(night, fake_model, subject) -> None:  # noqa: F811
    env, h, cid, _rid, svc = night
    _without_rebates(svc, cid)
    fake_model["out"] = {
        "changes": [
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
        ],
        "reply": "I'll add Rebates and take it off net revenue.",
        "clarify": None,
    }
    back = f"/ontology?panel=othor&subject={subject}"
    tok = env.csrf(h + "/ontology")
    env.client.post(
        h + "/othor/reply",
        data={"csrf": tok, "subject": subject, "text": LINE, "back": back},
    )
    assert fake_model["prompt"] == ("ontology_change", "v5")
    page = _page(env, h + back)
    assert "Try saying it another way" not in page
    assert "Apply 2 changes" in page and "Rebates" in page
    assert "Net revenue" in page.split("Apply 2 changes", 1)[0]
    # today's value before and after, computed (the preview through the read-only role)
    vals = re.findall(r'hx-get="(/othor/preview\?[^"]*item=c%3A2[^"]*)"', page)
    assert vals, page[-3000:]
    got = _page(env, h + html.unescape(vals[0]))
    assert "Net revenue today:" in got and "→" in got
    with svc.sf() as s, tenant_scope(cid):
        n = service.current_number(s)
    env.client.post(h + "/othor/apply", data={"csrf": tok, "subject": subject, "back": back})
    with svc.sf() as s, tenant_scope(cid):
        assert service.current_number(s) == n + 1
        keys = {r.key for r in service.active(s, ["metric"])}
        nr = next(r for r in service.active(s, ["metric"]) if r.key == "net_revenue")
    assert "rebates" in keys and "rebates" in json.dumps(nr.body["formula"])
    assert quote(subject)  # both boxes, one Apply
