# SPDX-License-Identifier: Apache-2.0
"""Chat 1.3 (owner, 20 Nov 2026): every setup decision in "Things only you can decide" opens with
Othor AI's suggested answer already chosen and why in one line, with Apply and Suggest edits as in
the one chat, instead of empty choices and "Use this answer". The values stay computed; a `why`
with a figure, or an id that isn't one of the choices, is discarded (no pick); a decision Othor AI
can't call stays open with no pick; without a model: no pick, as before. Setup decisions are the
owner's, so no "Drafts only" note. Needs `make dev && make seed` (the sample sources)."""

from __future__ import annotations

import html
from typing import Any

from othor.ai.gateway import Result
from othor.setup import decision_suggest as ds
from othor.web import setup_view as v
from tests.web.board_worlds import PREPS, World, setup_world  # noqa: F401
from tests.web.conftest import env  # noqa: F401
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_metrics_checks import _events, _rows

M = "/setup/metrics"
OPTS = [
    {"id": "source", "label": "Net of GST", "where": "erp_db · SQL"},
    {"id": "upload", "label": "Including GST", "where": "MIS · tab Sales"},
    {"id": "both", "label": "Show both in the brief", "where": "Each with its label"},
]


class FakeGateway:
    """The gateway as the draft job sees it: one answer for every decision, every call kept."""

    def __init__(self, out: dict[str, Any] | None, guard: str = "passed") -> None:
        self.out, self.guard, self.calls = out, guard, []

    def complete(self, company_id: Any, prompt: str, version: str, inputs: Any) -> Result:
        self.calls.append((prompt, version, inputs))
        return Result(self.out, "{}" if self.out else None, self.guard)


def _panel(w: World, key: str, extra: str = "") -> str:
    text = html.unescape(w.env.client.get(w.host + v.decision_url(key) + extra).text)
    return text.split('aria-label="Things only you can decide"', 1)[1].split("</aside>", 1)[0]


def test_the_answer_is_checked_by_code() -> None:
    ok = ds.check({"choice": "source", "why": "Your business message says net of GST."}, OPTS)
    assert ok == {"choice": "source", "why": "Your business message says net of GST."}
    # an id that isn't one of the choices, a figure in why, or no why: no pick (rule 1)
    assert ds.check({"choice": "mine", "why": "Because."}, OPTS)["choice"] == ""
    assert ds.check({"choice": "upload", "why": "The MIS shows ₹4.2 Cr."}, OPTS)["choice"] == ""
    assert ds.check({"choice": "upload", "why": ""}, OPTS)["choice"] == ""
    assert ds.check(None, OPTS)["choice"] == ""


def test_a_conflict_opens_with_the_suggestion_chosen_and_apply(setup_world: World) -> None:  # noqa: F811
    w = setup_world
    PREPS["metrics_drafted"](w)
    # without a model: no pick, the choices as before
    p = _panel(w, "net_revenue")
    assert 'name="choice:net_revenue"' in p and "Use this answer" in p and "My suggestion" not in p

    gw = FakeGateway(
        {"choice": "source", "why": "Your business message says net revenue excludes GST."}
    )
    assert ds.suggest_all(w.svc.sf, gw, w.cid) >= 1
    prompt, version, inputs = gw.calls[0]
    assert (prompt, version) == ("decision_suggest", "v1")
    # names and computed values only: no rows, no SQL
    assert {"metric", "question", "choices", "business", "files", "numbers"} <= set(inputs)
    assert "rows" not in str(inputs) and "SELECT" not in str(inputs)

    p = _panel(w, "net_revenue")
    assert "My suggestion" in p and "Your business message says net revenue excludes GST." in p
    assert ">Apply<" in p and "Suggest edits" in p
    assert "Use this answer" not in p and 'type="radio"' not in p
    assert 'name="choice:net_revenue" value="source"' in p
    # both values side by side, computed (the month and ₹)
    assert p.count('class="option"') == 2 and "Oct 2026 · ₹" in p
    # a setup decision is the owner's: no draft note (rule 6)
    assert "Drafts only" not in p

    # Suggest edits opens the reply box (the conflict chat), back to this decision
    e = _panel(w, "net_revenue", "&edit=1")
    assert 'id="oc-reply"' in e and f'name="back" value="{v.decision_url("net_revenue")}' in e
    assert "Suggest edits" not in e

    # Apply is the decision: saved at once, one event, the panel moves on
    r = w.post(f"{M}/decide", {"d": "net_revenue", "choice:net_revenue": "source"}, page=M)
    assert r.status_code == 303 and r.headers["location"] != v.decision_url("net_revenue")
    assert len(_events(w, "setup.conflict_settled")) == 1
    assert _rows(w)["net_revenue"].confidence != "conflict"


def test_no_pick_when_othor_ai_cannot_call_it(setup_world: World) -> None:  # noqa: F811
    w = setup_world
    PREPS["metrics_drafted"](w)
    for out in (
        {"choice": "", "why": ""},  # can't tell
        {"choice": "upload", "why": "The MIS total is 12 lakh higher."},  # a figure: discarded
    ):
        ds.suggest_all(w.svc.sf, FakeGateway(out), w.cid)
        p = _panel(w, "net_revenue")
        assert "My suggestion" not in p and 'name="choice:net_revenue"' in p
        assert " checked" not in p
    # the model unavailable: nothing stored, no pick (rule 4)
    assert ds.suggest_all(w.svc.sf, FakeGateway(None, "unavailable"), w.cid) == 0
    assert "My suggestion" not in _panel(w, "net_revenue")
