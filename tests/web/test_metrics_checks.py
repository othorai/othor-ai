# SPDX-License-Identifier: Apache-2.0
"""M11c · Metrics as three checks (m1–m8): the cards, the three panels, Confirm one and
"Confirm the N remaining" with Undo (one event per metric), keep or drop an assumption, and a
decision with nothing preselected. Needs `make dev && make seed` (the sample sources)."""

from __future__ import annotations

import re

import sqlalchemy as sa

from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.setup.models import SetupDraft
from tests.web.board_worlds import PREPS, World, setup_world  # noqa: F401
from tests.web.conftest import env  # noqa: F401
from tests.web.test_chat_web import need_sources, sample  # noqa: F401

M = "/setup/metrics"


def _rows(w: World, kind: str = "metric") -> dict[str, SetupDraft]:
    with w.svc.sf() as s, tenant_scope(w.cid):
        rows = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == kind)).all()
        s.expunge_all()
    return {r.key: r for r in rows}


def _events(w: World, action: str) -> list[Event]:
    with w.svc.sf() as s, tenant_scope(w.cid):
        ev = s.scalars(sa.select(Event).where(Event.action == action)).all()
        s.expunge_all()
    return list(ev)


def test_three_checks_confirm_all_and_undo(setup_world: World) -> None:  # noqa: F811
    w = setup_world
    PREPS["metrics_drafted"](w)
    page = w.env.client.get(w.host + M).text
    for text in ("Assumptions", "Decisions", "Metrics to confirm", "Needs you", "Good to go"):
        assert text in page, text
    assert "1 required decision left before you continue" in page

    # Decisions: the conflict's two computed values, nothing preselected; the waiting source
    dec = w.env.client.get(w.host + M + "?panel=decisions").text
    assert "Check 2 of 3" in dec and 'name="choice:net_revenue"' in dec
    assert " checked" not in dec.split('action="/setup/metrics/decide"')[1].split("</form>")[0]
    assert "Required" in dec and "Decision 1 of 2" in dec  # #32: one per screen
    dec = w.env.client.get(w.host + M + "?panel=decisions&d=order_book_value").text
    assert "Optional" in dec and "Set aside for now" in dec

    # Metrics to confirm: two groups, values computed; confirm one, then undo it
    panel = w.env.client.get(w.host + M + "?panel=confirm").text
    assert "Check 3 of 3" in panel and "You can change any of these later in the Ontology." in panel
    remaining = re.findall(r'name="key" value="([a-z0-9_]+)"', panel.split("confirm-all")[1])
    assert remaining, "the footer posts the remaining keys"
    first = remaining[0]
    r = w.post(f"{M}/{first}/confirm", {"next": f"{M}?panel=confirm"}, page=M)
    assert r.headers["location"] == f"{M}?panel=confirm"
    assert _rows(w)[first].state == "confirmed"
    r = w.post(f"{M}/unconfirm", {"key": first, "next": f"{M}?panel=confirm"}, page=M)
    assert _rows(w)[first].state == "needs_confirmation"

    # Confirm the N remaining: one request, one event per metric, then Undo the batch
    n0 = len(_events(w, "setup.draft_confirmed"))
    r = w.post(f"{M}/confirm-all", {"key": remaining}, page=M)  # type: ignore[dict-item]
    loc = r.headers["location"]
    assert loc.startswith(f"{M}?panel=confirm&done=")
    done = loc.split("done=")[1].replace("%2C", ",").split(",")
    assert sorted(done) == sorted(remaining)
    assert len(_events(w, "setup.draft_confirmed")) - n0 == len(remaining)
    toast = w.env.client.get(w.host + loc).text
    assert f"{len(remaining)} metric" in toast and "confirmed" in toast and "Undo" in toast
    assert all(_rows(w)[k].state == "confirmed" for k in remaining)
    w.post(f"{M}/unconfirm", {"key": remaining}, page=M)  # type: ignore[dict-item]
    assert all(_rows(w)[k].state == "needs_confirmation" for k in remaining)
    assert len(_events(w, "setup.draft_unconfirmed")) >= len(remaining) + 1
    # a conflict is never confirmed by confirm-all
    w.post(f"{M}/confirm-all", {"key": ["net_revenue"]}, page=M)
    assert _rows(w)["net_revenue"].state != "confirmed"


def test_assumption_change_keeps_or_drops(setup_world: World) -> None:  # noqa: F811
    w = setup_world
    PREPS["metrics_drafted"](w)
    found = _rows(w, "discovery")
    assert found, "the sample company has assumptions"
    key = sorted(found)[0]
    page = w.env.client.get(w.host + M + "?panel=assumptions").text
    assert "What we assumed" in page and f"panel=assumptions&amp;key={key}" in page
    one = w.env.client.get(w.host + M + f"?panel=assumptions&key={key}").text
    assert "All assumptions" in one and "Don’t use this rule" in one
    r = w.post(f"{M}/found/{key}/set", {"use": "skip", "next": f"{M}?panel=assumptions"}, page=M)
    assert r.headers["location"] == f"{M}?panel=assumptions"
    assert _rows(w, "discovery")[key].state == "skipped"
    assert _events(w, "setup.discovery_skip")


def test_suggested_connect_buttons_are_all_plain(setup_world: World) -> None:  # noqa: F811
    """#19: every Connect in "Suggested from your files" looks the same; the one main (blue)
    button on the step is Continue."""
    w = setup_world
    PREPS["at_sources_step"](w)
    page = w.env.client.get(w.host + "/setup/sources").text
    box = page.split('aria-label="Suggested from your files"', 1)[1].split("</section>", 1)[0]
    connects = re.findall(r'<a [^>]*class="([^"]*btn[^"]*)"[^>]*>\s*Connect\s*</a>', box)
    assert len(connects) >= 2, box[:400]
    assert not [c for c in connects if "primary" in c.split()], connects


def test_open_the_brief_finishes_setup(setup_world: World) -> None:  # noqa: F811
    """#22: "Open the brief" on setup's last screen was a plain link, so setup stayed on its
    last step and every sign-in came back to it. It now finishes setup, then opens the brief."""
    from othor.web.routes.auth import resume_path

    w = setup_world
    PREPS["first_run_done"](w)
    page = w.env.client.get(w.host + "/setup/first-run").text
    foot = page.split('action="/setup/first-run/continue"', 1)[1].split("</form>", 1)[0]
    assert re.search(r'<button[^>]*name="next"[^>]*value="/brief"[^>]*>\s*Open the brief', foot)
    r = w.post("/setup/first-run/continue", {"next": "/brief"}, page="/setup/first-run")
    assert r.status_code == 303 and r.headers["location"] == "/brief"
    assert resume_path(w.svc, w.cid) == "/home"
    # anything but /brief goes to Home, as before
    r = w.post("/setup/first-run/continue", {"next": "https://evil.example/"}, page="/home")
    assert r.headers["location"] == "/home"
