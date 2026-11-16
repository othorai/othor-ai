# SPDX-License-Identifier: Apache-2.0
"""Actions happen in place (docs/07, M12b): an owner action is a form marked `data-inplace`
(the `action_form` macro). othor.js posts it, follows the 303 and swaps #main and #overlay from
the page it lands on, so nothing reloads and every count on the page updates with it; with
JavaScript off it's the same plain post. A failed in-place post comes back as the 9a toast.

Home first (3a, 3aa): Accept, Decline, Undo, the question replies, Skip, Undo and Dismiss.
Needs `make dev && make seed`.
"""

from __future__ import annotations

import re
from pathlib import Path

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.engine.models import Proposal
from tests.web.conftest import Env
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

INPLACE = {"X-Othor-Inplace": "1"}
FORM = re.compile(r"<form\b[^>]*>", re.S)


def _forms(html: str) -> list[str]:
    return [f for f in FORM.findall(html) if 'method="post"' in f]


def _post(env: Env, h: str, path: str, data: dict | None = None, **kw):
    tok = env.csrf(h + "/home")
    return env.client.post(h + path, data={"csrf": tok, **(data or {})}, **kw)


def _open(html: str) -> int:
    """The proposals still open on Home: rows with an Accept form (Home's header count went in
    design B2, 11 Nov 2026, so the rows are the count)."""
    return len(re.findall(r'action="/home/proposals/[^"/]+/accept"', html))


def test_every_home_action_is_in_place(night):  # noqa: F811
    env, h, *_ = night
    for path in ("/home", "/home?panel=questions"):
        forms = _forms(env.client.get(h + path).text)
        assert forms, path
        plain = [f for f in forms if "data-inplace" not in f and "/logout" not in f]
        assert plain == [], (path, plain)


def test_the_undo_row_sits_where_the_proposal_was(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        rows = s.scalars(
            sa.select(Proposal)
            .where(Proposal.status == "open", Proposal.kind != "join")
            .order_by(Proposal.created_at, Proposal.id)
        ).all()
        ids = [str(p.id) for p in rows]
    assert ids, "the night has an open proposal"
    before = env.client.get(h + "/home").text
    assert _open(before) == len(ids)
    pid = ids[0]
    r = _post(env, h, f"/home/proposals/{pid}/accept", follow_redirects=True, headers=INPLACE)
    assert r.status_code == 200
    page = r.text
    rows_now = re.findall(r'data-row="([^"]+)"', page.split('aria-label="Proposals"', 1)[1])
    assert rows_now[: len(ids)] == ids, "the decided row keeps its place"
    assert "Accepted · applies from tonight’s run" in page
    assert f'action="/home/proposals/{pid}/undo"' in page
    assert _open(page) == len(ids) - 1
    # Undo puts the row back as it was
    r = _post(env, h, f"/home/proposals/{pid}/undo", follow_redirects=True, headers=INPLACE)
    assert _open(r.text) == len(ids)
    assert f'action="/home/proposals/{pid}/accept"' in r.text


def test_a_failed_in_place_post_is_a_toast_not_a_page(night):  # noqa: F811
    env, h, *_ = night
    r = _post(
        env,
        h,
        "/home/proposals/00000000-0000-0000-0000-000000000000/accept",
        follow_redirects=False,
        headers=INPLACE,
    )
    assert r.status_code == 404
    assert r.headers.get("X-Othor-Toast") == "1"
    assert "<html" not in r.text and 'class="toast' in r.text


# Posts that go to another page (docs/07: "a full-page navigation is right only when the owner
# goes to another page"), with the reason. Every other post form is in place.
NAVIGATION = {
    '"/login"': "sign-in: the next step is another page",
    '"/signup"': "signup: the next step is another page",
    '"/signup/address"': "signup: hands off to the company host",
    '"{{ action }}"': "the code card: a verified code signs in",
    "resend_action": "the code card: a new code is the same step, re-rendered",
    '"/logout"': "sign out",
    '"/logout/everywhere"': "sign out everywhere (Account): this session ends too, so sign-in",
    "/setup/upload/continue": "onboarding: Continue goes to the next step",
    "/setup/business/continue": "onboarding: Continue goes to the next step",
    "/setup/sources/continue": "onboarding: Continue goes to the next step",
    "/setup/metrics/confirm": "onboarding: Confirm and continue goes to the first run",
    "/setup/first-run/continue": "onboarding: the first run's Continue goes to Home",
    "/brief/new": "a new brief opens its own page",
    "/sources/google/start": "Google sign-in (OAuth) is another site",
    'id="connect-form"': "the connect form: Connect goes to the scan (its buttons use htmx)",
    'id="picked-files"': "the Google Picker posts the chosen files and moves to the next step",
}
POST = re.compile(r'(<form\b[^>]*method="post"[^>]*>|el\(\s*"form"[^%]*?post[^%]*?%\})', re.S)


def test_every_post_form_is_in_place_or_a_listed_navigation() -> None:
    root = Path(__file__).resolve().parents[2] / "othor/web/templates"
    plain, seen = [], 0
    for f in sorted(root.rglob("*.html")):
        for m in POST.finditer(f.read_text()):
            seen += 1
            t = m.group(0).replace("'", '"')
            if "data-inplace" in t or "hx-" in t or "action_form" in t:
                continue
            if any(k in t for k in NAVIGATION):
                continue
            plain.append(f"{f.relative_to(root)}: {t[:120]}")
    assert seen > 60, seen  # the scan sees the app's forms
    assert plain == [], "post forms that reload the page:\n" + "\n".join(plain)
