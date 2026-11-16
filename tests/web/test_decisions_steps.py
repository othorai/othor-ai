# SPDX-License-Identifier: Apache-2.0
"""#32 · Things only you can decide, one decision per screen (the questions panel's style):
"Decision 2 of 6 · 1 decided" and its segments, Back · Next that only move, each answer saved as
it's made and the panel moving on, set-aside ones counted, the last one closing the panel, a
required conflict still blocking the first run. Needs `make dev && make seed`."""

from __future__ import annotations

import html
import re

from othor.web import setup_view as v
from tests.web.board_worlds import PREPS, World, setup_world  # noqa: F401
from tests.web.conftest import env  # noqa: F401
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_metrics_checks import _events, _rows

M = "/setup/metrics"
D = f"{M}?panel=decisions"


def _get(w: World, url: str) -> str:
    return html.unescape(w.env.client.get(w.host + url).text)


def _panel(text: str) -> str:
    return text.split('aria-label="Things only you can decide"', 1)[1].split("</aside>", 1)[0]


def _steps(text: str) -> tuple[int, int, int]:
    m = re.search(r"<b>Decision (\d+) of (\d+)</b> · (\d+) decided", text)
    assert m, text[:600]
    return int(m[1]), int(m[2]), int(m[3])


def _nav(text: str) -> tuple[str | None, str]:
    nav = text.split('class="qp-nav" aria-label="Decisions"', 1)[1].split("</nav>", 1)[0]
    back = re.search(r'<a class="back" href="([^"]+)"', nav)
    nxt = re.search(r'<a class="next" href="([^"]+)"', nav)
    assert nxt, nav
    return (back[1] if back else None), nxt[1]


def _order(w: World, start: str = D) -> list[str]:
    """The fixed order, walked with Next from the first screen."""
    keys, url = [], start
    while True:
        p = _panel(_get(w, url))
        key = re.search(r'name="d" value="([^"]+)"|/setup/metrics/([a-z0-9_]+)/(?:skip|reopen)', p)
        assert key, p[:600]
        keys.append(key[1] or key[2])
        _, nxt = _nav(p)
        if nxt == M:
            return keys
        url = nxt


def test_one_decision_per_screen_with_back_and_next(setup_world: World) -> None:  # noqa: F811
    w = setup_world
    PREPS["metrics_drafted"](w)
    page = _get(w, D)
    p = _panel(page)
    at, total, done = _steps(p)
    assert (at, done) == (1, 0) and total >= 2
    assert (
        len(re.findall(r'<li class="seg[ "]', p)) == total and p.count('aria-current="step"') == 1
    )
    # the first is the required conflict: its options as radio cards, none picked
    assert 'name="choice:net_revenue"' in p and " checked" not in p
    assert "Required" in p and "Which should net revenue use?" in p
    assert "Show both in the brief" in p and "Oct 2026 · ₹" in p
    # one decision only; the old one-Save form and its count are gone
    assert p.count('class="nb-q"') == 1
    assert 'id="decide-form"' not in page and "of " + str(total) + " decided" not in p
    # the answers here are the owner's, so no draft note (owner, 12 Nov 2026, #33)
    assert "Drafts only" not in p
    back, nxt = _nav(p)
    assert back is None and nxt.startswith(D + "&d=")

    # Next only moves: nothing is saved; Back comes back
    keys = _order(w)
    assert len(keys) == total and keys[0] == "net_revenue"
    second = _panel(_get(w, nxt))
    assert _steps(second) == (2, total, 0)
    assert _nav(second)[0] == v.decision_url("net_revenue")
    assert _rows(w)["net_revenue"].confidence == "conflict"
    # the last screen's Next is Done, which closes the panel
    last = _panel(_get(w, v.decision_url(keys[-1])))
    assert ">Done" in last.split("qp-nav", 1)[1] and _nav(last)[1] == M

    # a needs_source decision: its own action and Set aside, on its own screen
    ob = _panel(_get(w, v.decision_url("order_book_value")))
    assert "Optional" in ob and "Set aside for now" in ob
    assert "Without it, this metric is set aside for now." in ob
    assert re.search(r"Connect|Where is it\?|Tell Othor AI", ob)
    assert 'name="choice:' not in ob


def test_answers_save_in_place_and_move_on(setup_world: World) -> None:  # noqa: F811
    w = setup_world
    PREPS["metrics_drafted"](w)
    keys = _order(w)
    total = len(keys)
    page = _get(w, M)
    assert "1 required decision left before you continue" in page
    # a required conflict blocks the first run until it's settled
    r = w.post(f"{M}/confirm", page=M)
    assert r.headers["location"] == M

    # nothing picked: it stays
    r = w.post(f"{M}/decide", {"d": "net_revenue"}, page=M)
    assert r.headers["location"] == v.decision_url("net_revenue")
    assert _rows(w)["net_revenue"].confidence == "conflict"

    # pick: saved at once (one event), the panel moves on to the next open one
    r = w.post(f"{M}/decide", {"d": "net_revenue", "choice:net_revenue": "source"}, page=M)
    assert r.headers["location"] == v.decision_url(keys[1])
    assert len(_events(w, "setup.conflict_settled")) == 1
    page = _get(w, r.headers["location"])
    assert _steps(_panel(page)) == (2, total, 1)
    assert "required decision" not in page.split('aria-label="Things only you can decide"')[0]

    # Back to it: decided, with what it did and Change; still first in the order
    settled = _panel(_get(w, v.decision_url("net_revenue")))
    assert "You decided" in settled and "erp_db wins" in settled and ">Change<" in settled
    assert _order(w, v.decision_url("net_revenue")) == keys

    # Set aside the optional ones (even while the conflict's values compute): each counts as
    # decided and moves on; the last closes the panel
    for n, key in enumerate(keys[1:], 2):
        r = w.post(f"{M}/{key}/skip", {"next": D}, page=M)
        want = v.decision_url(keys[n]) if n < total else M
        assert r.headers["location"] == want, (key, r.headers["location"])
    assert all(_rows(w)[k].state == "skipped" for k in keys[1:])
    p = _panel(_get(w, D))
    assert _steps(p) == (total, total, total)  # all decided: the last one shows
    assert p.count('class="seg done') == total
    aside = _panel(_get(w, v.decision_url(keys[1])))
    assert "Set aside" in aside and "Bring it back" in aside
    assert len(_events(w, "setup.draft_skipped")) == total - 1

    # nothing required is left: the first run isn't blocked
    from tests.web.board_worlds_setup import _jobs

    _jobs(w)
    page = _get(w, M)
    assert "Ready for your first run" in page and "required decision" not in page


def test_change_opens_a_decision_again(setup_world: World) -> None:  # noqa: F811
    w = setup_world
    PREPS["metrics_drafted"](w)
    from tests.web.board_worlds_setup import _jobs

    w.post(f"{M}/decide", {"d": "net_revenue", "choice:net_revenue": "upload"}, page=M)
    _jobs(w)
    r = w.post(f"{M}/net_revenue/reopen", page=M)
    assert r.headers["location"] == v.decision_url("net_revenue")
    row = _rows(w)["net_revenue"]
    assert row.confidence == "conflict" and (row.value or {}).get("conflict")
    assert "settled" not in (row.value or {})
    assert len(_events(w, "setup.decision_reopened")) == 1
    p = _panel(_get(w, r.headers["location"]))
    assert 'name="choice:net_revenue"' in p and _steps(p)[2] == 0
    assert "1 required decision left before you continue" in _get(w, M)

    # a set-aside one comes back as it was
    w.post(f"{M}/order_book_value/skip", {"next": D}, page=M)
    assert _rows(w)["order_book_value"].state == "skipped"
    w.post(f"{M}/order_book_value/reopen", page=M)
    assert _rows(w)["order_book_value"].state == "needs_source"


def test_a_redrafted_definition_is_a_decision_with_change(setup_world: World) -> None:  # noqa: F811
    """A redrafted definition (2nf) is a required decision of its own: accept or keep, saved at
    once; it stays listed as decided, and Change puts the question back."""
    import sqlalchemy as sa

    from othor.core.tenancy import tenant_scope
    from othor.setup.models import SetupDraft
    from tests.web.board_worlds_setup import _jobs

    w = setup_world
    PREPS["metrics_drafted"](w)
    with w.svc.sf() as s, tenant_scope(w.cid):
        row = s.scalars(
            sa.select(SetupDraft).where(
                SetupDraft.kind == "metric", SetupDraft.state == "needs_confirmation"
            )
        ).first()
        assert row is not None
        key = row.key
        row.prev_body = {**row.body, "description": "The old definition."}
        row.changed = True
        s.commit()
    p = _panel(_get(w, v.decision_url(key)))
    assert "Use the redrafted definition?" in p and "Required" in p
    assert f'name="changed:{key}"' in p and "The old definition." in p
    r = w.post(f"{M}/decide", {"d": key, f"changed:{key}": "reject"}, page=M)
    assert r.headers["location"] == v.decision_url("order_book_value")
    got = _rows(w)[key]
    assert not got.changed and got.body["description"] == "The old definition."
    assert len(_events(w, "setup.draft_rejected")) == 1
    p = _panel(_get(w, v.decision_url(key)))
    assert "Kept your definition." in p and ">Change<" in p
    _jobs(w)
    assert "Kept your definition." in _panel(_get(w, v.decision_url(key)))  # kept by a recompute
    w.post(f"{M}/{key}/reopen", page=M)
    got = _rows(w)[key]
    assert got.changed and (got.prev_body or {}).get("description") == "The old definition."
