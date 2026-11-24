# SPDX-License-Identifier: Apache-2.0
"""The Morning Brief page on the steady 1 Nov fixture (4d, 4d1, 4g): the stored issue as the run
made it, P1 first, tiles, Ask today, "N more in Sweep" from the same overlay as Sweep, the full
detail, the finding panel over the brief with its context; another company sees a 404."""

from __future__ import annotations

import html
import json
import re

import sqlalchemy as sa

from othor.briefs.models import Brief
from othor.core.tenancy import tenant_scope
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import _fp, night  # noqa: F401


def _brief_id(svc, cid):
    with svc.sf() as s, tenant_scope(cid):
        return s.scalars(sa.select(Brief.id)).one()


def _text(page: str) -> str:
    """The page's words as a reader sees them: tags out, spaces collapsed."""
    import html as _h

    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", page)))


def _panel(page: str) -> str:
    return page[page.index('id="overlay"') :]


def test_the_brief_opens_with_p1_first(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid = _brief_id(svc, cid)
    briefs = env.client.get(h + "/brief").text  # 4a: the first run made one brief
    for text in (
        "Morning Brief",
        "Leadership",
        "Default",
        "Ready · 6:00 AM IST",
        "Whole company",
        "New brief",
        # New brief is a card among the briefs (v0.15 design, in v0.11)
        'class="brief-card new" href="/brief/new"',
        # M15.3: "Briefs · N", each card's findings and Open brief →; no First run table
        "Briefs · 1",
        "4 findings",
        "Open brief →",
        f"/brief/{bid}",
        "Archive",
    ):
        assert text in briefs, text
    # the top bar's ⋯ menu on every page (owner, 15 Nov 2026; 23 Nov 2026): Archive is in it
    more = briefs[briefs.index('class="use top-more"') :]
    more = more[: more.index("</details>")]
    assert 'role="menuitem" href="/brief/archive">Archive<' in more
    assert briefs.count('class="use top-more"') == 1
    # #44: the New brief card is the icon and "New brief" only, no description or link line
    card = briefs[briefs.index('class="brief-card new"') :][:600]
    card = card[: card.index("</a>")]
    assert "New brief" in card and 'class="meta"' not in card and "Create a brief" not in card
    assert "Question for: Priya Menon" in briefs
    assert "This morning" not in briefs and ">First run<" not in briefs
    page = env.client.get(h + f"/brief/{bid}").text
    for text in (
        "Leadership",
        # 0.28.4 (owner, 19 Nov 2026): the name with an (i) (run, sources, order), the day and time
        'class="brief-kicker"',
        'aria-label="About this brief"',
        "by money, how soon, and how sure",
        "Sun 1 Nov · 6:00 AM IST",
        "South fell ₹1.4 Cr while West’s growth hid it; three accounts carry",
        "Headline numbers",
        "Net revenue",
        "₹4.62 Cr",
        "What needs you · 4",
        'class="consequence"',
        "Question to ask today",
        "for Priya Menon · Head of Sales",
        "Settles item 01.",
        "more found last night, not in this brief",
        "See them in Sweep →",
        "Recipients · ",
    ):
        assert text in page, text
    # 0.28.4: no "from last night's run", no Ask line under the title (Ask stays at the foot)
    assert "from last night’s run" not in page and 'class="ask-line"' not in page
    # the run line is Full detail's (the design drops it from Simple)
    assert "Every number is computed" not in page
    # cards numbered 01–04, Snooze and Mute returning to this brief
    assert re.findall(r'<span class="n">(\d\d)</span>', page)[:4] == ["01", "02", "03", "04"]
    # (the cards' own: What to do's rows after them have theirs, test_what_to_do_web)
    cards = page.split('aria-label="Action plan"', 1)[0]
    assert cards.count(f"&amp;back=brief-{bid}") == 8 and 'id="item-1"' in page
    titles = re.findall(r'<a class="title" href="[^"]+">([^<]+)<', page)
    assert titles[0] == "South fell ₹1.4 Cr; West’s rise hid it" and len(titles) == 4
    assert "<svg" in page  # tiles and item charts from ChartSpec
    # #36, #37: "More info" and the chart open the finding's panel at its first tab (Finding),
    # the same address as the title; "How is this calculated?" is gone from the cards
    first = re.search(r'<a class="title" href="([^"]+)">', page).group(1)
    assert "tab=test" not in first
    assert f'<a class="chart visual" href="{first}"' in page
    assert "More info" not in page  # 0.28.4 (owner): the chart and the title open it
    assert "How is this calculated?" not in page.split('class="brief-items"', 1)[1]


def test_full_detail_and_the_panel(night):  # noqa: F811
    env, h, cid, rid, svc = night
    bid = _brief_id(svc, cid)
    full = env.client.get(h + f"/brief/{bid}?view=full").text
    assert "Situation" in full and "Needs attention · 4" in full
    assert "Since yesterday’s brief" in full
    assert 'class="view-switch"' in full and "Every number is computed" in full
    assert "· replayable" in full and "template prose" in full
    p1 = _fp(svc, cid, rid, "net_hiding_gross")
    panel = env.client.get(h + f"/brief/{bid}/finding/{p1}?date=2026-11-01").text
    assert "Leadership brief · Sun 1 Nov · In the brief · day 1" in panel
    test = env.client.get(h + f"/brief/{bid}/finding/{p1}?date=2026-11-01&tab=test").text
    assert "Benjamini–Hochberg" in test
    # a finding that isn't one of the brief's items isn't opened from the brief
    p10 = _fp(svc, cid, rid, "cross_source")
    assert env.client.get(h + f"/brief/{bid}/finding/{p10}").status_code == 404
    assert env.client.get(h + "/brief/not-an-id").status_code == 404
    assert env.client.get(h + f"/brief/{bid}?date=2026-02-31").status_code == 404


def test_the_archive_lists_issues_and_why_a_night_failed(night):  # noqa: F811
    import datetime as dt

    from othor.engine.models import Run, RunStep

    env, h, cid, rid, svc = night
    bid = _brief_id(svc, cid)
    with svc.sf() as s, tenant_scope(cid):
        last = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        failed = Run(
            number=last.number + 1,
            kind="nightly",
            run_date=dt.date(2026, 11, 2),
            spec_id=last.spec_id,
            image_version="0.1.0",
            started_at=last.started_at,
            status="failed",
        )
        s.add(failed)
        s.flush()
        s.add(
            RunStep(
                run_id=failed.id,
                name="read:erp_db",
                ordinal=0,
                status="failed",
                error="erp_db refused the connection (password expired)",
            )
        )
        s.commit()
        n = failed.number
    page = env.client.get(h + "/brief/archive?month=2026-11").text
    for text in (
        "Archive",
        "November 2026",
        "All briefs",
        "Leadership",
        "Sun 1 Nov",
        "failed · not made",
        "Why it failed",
        "Runs OK",
        "last 31 days",
        "Promoted into briefs",
        "no replay checked yet",
    ):
        assert text in page, text
    why = env.client.get(h + f"/brief/archive?month=2026-11&failed={n}").text
    assert f"Run #{n:04d}" in why and "Failed while reading erp_db" in why
    assert "erp_db refused the connection (password expired)" in why
    assert "Nightly run for Leadership" in why
    # the brief page says there's no brief this morning, and keeps yesterday's
    today = env.client.get(h + f"/brief/{bid}").text
    assert "No brief this morning." in today and "Open yesterday’s brief" in today
    assert env.client.get(h + "/brief/archive?failed=9999").status_code == 404
    assert env.client.get(h + "/brief/archive?month=2026-13").status_code == 404


def _edit(env, h, bid, **fields):
    import json

    page = env.client.get(h + f"/brief/{bid}/edit?tab={fields.get('tab', 'question')}").text
    draft = re.search(r'name="draft" value="([^"]*)"', page).group(1)
    import html as _h

    data = {"csrf": env.csrf(h + f"/brief/{bid}"), "draft": _h.unescape(draft), **fields}
    return env.client.post(h + f"/brief/{bid}/edit", data=data, follow_redirects=False), json


def test_edit_brief_shows_the_effect_today_and_saves_a_version(night):  # noqa: F811
    from othor.briefs.models import BriefVersion
    from othor.core.models import Event

    env, h, cid, _rid, svc = night
    bid = _brief_id(svc, cid)
    q = env.client.get(h + f"/brief/{bid}/edit").text
    for text in (
        "Edit brief · Leadership",
        "v1 · last changed",
        "Question",
        "Scope",
        "Numbers",
        "Delivery",
        "Othor AI",
        "The question this brief answers",
        "Deadlines it watches",
        "Ask today goes to",
        "Today with this change · computed",
        "No changes yet",
        "Applies from tomorrow’s run",
    ):
        assert text in q, text
    # M11c G: the reason sits beside the greyed Save changes, not only in the effect box
    assert re.search(r'>Change something to save</span>\s*<a class="btn"[^>]*>Cancel</a>', q)
    # Scope: leave out Export, every other chip as the form posts it; the effect is computed
    # before saving
    scope = env.client.get(h + f"/brief/{bid}/edit?tab=scope").text
    chips: dict[str, list[str]] = {}
    for name, value in re.findall(r'name="(keep:[^"]+)" value="([^"]+)" checked', scope):
        chips.setdefault(name, []).append(value)
    assert "Export" in chips["keep:region"]
    chips["keep:region"] = [v for v in chips["keep:region"] if v != "Export"]
    r, _ = _edit(env, h, bid, tab="scope", action="preview", **chips)
    assert r.status_code == 200
    assert "Today with this change · computed" in r.text and "items" in r.text
    assert "was ₹4.62 Cr" in r.text and "saves as v2" in r.text
    draft = re.search(r'name="draft" value="([^"]*)"', r.text).group(1)
    import html as _h

    r = env.client.post(
        h + f"/brief/{bid}/edit",
        data={
            "csrf": env.csrf(h + f"/brief/{bid}"),
            "draft": _h.unescape(draft),
            "tab": "scope",
            "action": "save",
            **chips,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == f"/brief/{bid}?saved=2"
    assert "Saved as v2" in env.client.get(h + r.headers["location"]).text
    with svc.sf() as s, tenant_scope(cid):
        v2 = s.scalars(sa.select(BriefVersion).where(BriefVersion.number == 2)).one()
        assert v2.body["scope"]["exclude"] == {"region": ["Export"]}
        assert "scope" in v2.diff
        assert s.scalars(sa.select(Event).where(Event.action == "brief.changed")).one()
    # Numbers: remove one, add one back from the search (4d5)
    r, _ = _edit(env, h, bid, tab="numbers", action="remove:dso")
    assert (
        "dso"
        not in re.search(r'name="draft" value="([^"]*)"', r.text).group(1).split("headline")[1][:80]
    )
    add = env.client.get(h + f"/brief/{bid}/edit?tab=add&q=receivables").text
    assert "Receivables" in add and "Add" in add


def test_recipients_and_the_one_line_change(night, monkeypatch):  # noqa: F811
    import json

    from othor.ai.gateway import ProviderReply
    from othor.briefs.models import Recipient
    from othor.web.routes import brief as brief_routes

    env, h, cid, _rid, svc = night
    bid = _brief_id(svc, cid)
    tok = env.csrf(h + f"/brief/{bid}")
    r = env.client.post(
        h + f"/brief/{bid}/recipients",
        data={"csrf": tok, "r_name": "Priya Menon", "r_email": "priya@indigothread.example"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = env.client.get(h + f"/brief/{bid}/edit?tab=delivery").text
    assert (
        "priya@indigothread.example" in page
        and "Recipients get the email and a read-only link." in page
    )
    with svc.sf() as s, tenant_scope(cid):
        rid = s.scalars(
            sa.select(Recipient.id).where(Recipient.email == "priya@indigothread.example")
        ).one()
    env.client.post(h + f"/brief/{bid}/recipients/{rid}/remove", data={"csrf": tok})
    assert (
        "priya@indigothread.example"
        not in env.client.get(h + f"/brief/{bid}/edit?tab=delivery").text
    )

    class Fake:
        name, configured = "fake", True

        def call(self, system, messages, schema, max_tokens=0):
            out = {"exclude": {"region": ["Export"]}, "reply": "Export is left out."}
            return ProviderReply(json.dumps(out), "fake", 1, 1)

    monkeypatch.setattr(brief_routes, "provider_for", lambda settings: Fake())
    scope = env.client.get(h + f"/brief/{bid}/edit?tab=scope").text
    chips: dict[str, list[str]] = {}
    for name, value in re.findall(r'name="(keep:[^"]+)" value="([^"]+)" checked', scope):
        chips.setdefault(name, []).append(value)
    r, _ = _edit(env, h, bid, tab="scope", action="ai", line="leave out Export", **chips)
    assert "Export is left out." in r.text
    assert "was ₹4.62 Cr" in r.text  # the line became a typed change with its computed effect


def test_a_new_brief_from_one_question_then_undo(night):  # noqa: F811
    import html as _h

    from othor.briefs.models import Brief as B

    env, h, cid, _rid, svc = night
    ask = env.client.get(h + "/brief/new").text
    assert "What do you want to know every morning?" in ask and "Draft brief" in ask
    tok = env.csrf(h + "/brief")
    r = env.client.post(
        h + "/brief/new", data={"csrf": tok, "question": "Are my top 4 accounts still buying?"}
    )
    page = r.text
    text = _text(page)
    for line in (
        "Step 2 of 2 · Review the draft",
        "Drafted by rules · nothing is sent until you create it",
        "Change anything",
        "Create brief",
        "Are my top 4 accounts still buying?",
        "Drafts only. Numbers are computed, not written. Nothing changes until you apply.",
    ):
        assert line in text, line
    # the model is off in tests: the default numbers with the question, and one amber line
    assert "Drafted by rules: Othor AI isn’t available right now" in text
    draft = _h.unescape(re.search(r'name="draft" value="([^"]*)"', page).group(1))
    r = env.client.post(
        h + "/brief/new/create", data={"csrf": tok, "draft": draft}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/brief?created=")
    listing = env.client.get(h + r.headers["location"]).text
    assert "created · first brief tomorrow 6:00 AM" in listing and "Undo" in listing
    with svc.sf() as s, tenant_scope(cid):
        new = s.scalars(sa.select(B).where(B.is_default.is_(False))).one()
    # 0.29.4: the new brief opens with its first issue from last night's results (the empty
    # state of #96 stays for a brief made before any run: test_brief_instant)
    first = env.client.get(h + f"/brief/{new.id}")
    assert first.status_code == 200, first.status_code
    assert "From last night’s results · first scheduled issue tomorrow" in _text(first.text)
    r = env.client.post(
        h + f"/brief/{new.id}/undo-create", data={"csrf": tok}, follow_redirects=False
    )
    assert r.status_code == 303
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalars(sa.select(B.status).where(B.id == new.id)).one() == "archived"


def test_new_brief_step_1_in_the_questions_panel_style(night):  # noqa: F811
    """Step 1 (v0.11; calm, #95): the step bar, the box as the Othor AI chat's (no mark in it)
    with Draft brief under it, suggested questions as rows that fill the box (they don't send),
    each with where it came from, and the note. While it drafts the panel says so (#27)."""
    env, h, _cid, _rid, _svc = night
    page = env.client.get(h + "/brief/new").text
    panel = _panel(page)
    text = _text(panel)
    assert "Step 1 of 2 · Your question" in text
    assert 'class="nb-ai"' not in panel and "ai-box-mark" not in panel  # #95: no logo in the box
    assert '<textarea class="input" id="f-question" name="question"' in panel
    assert "Suggested for " in text and 'class="nb-suggest"' in panel
    assert (
        "Drafts only. Numbers are computed, not written. Nothing changes until you apply." in text
    )
    # the cards fill the box: buttons that don't submit, each with where it came from
    cards = re.findall(r'<button class="nb-card" type="button" data-tell-fill="([^"]+)"', panel)
    assert 1 <= len(cards) <= 3
    assert re.search(r'<span class="from">From your (goal|business) · ', panel)
    assert 'type="submit" name="question"' not in panel  # the old chips that posted at once
    # #27: the form posts in place, the button turns "Drafting…" and the line shows
    assert re.search(
        r'<form method="post" action="/brief/new" class="contents" data-inplace>', panel
    )
    assert 'data-busy="Drafting…"' in panel and "data-busy-line" in panel
    assert "Othor AI is drafting your brief…" in panel
    # Draft brief right under the box (the × closes the panel; no Cancel)
    assert panel.index("data-fill-target") < panel.index("Draft brief") and ">Cancel<" not in panel


def test_new_brief_review_is_a_table_not_chips(night):  # noqa: F811
    """Step 2 (#28): the draft as labelled rows with Change on hover and focus, the numbers'
    count named, the name from the question (never the default brief's); no preview of tomorrow's
    brief (#95), and Change anything as one line with Send beside it and the examples as chips."""
    env, h, _cid, _rid, _svc = night
    tok = env.csrf(h + "/brief")
    q = "Are my top 4 accounts still buying?"
    page = env.client.post(h + "/brief/new", data={"csrf": tok, "question": q}).text
    panel = _panel(page)
    heads = re.findall(r'<th scope="row">([^<]+)</th>', panel)
    assert heads == ["Question", "Scope", "Numbers", "Ask today", "Schedule", "First sent to"]
    assert len(re.findall(r'class="link ont-act nb-change" type="button"', panel)) == 5
    assert 'class="chip lg"' not in panel.split("Change anything")[0]  # no tag look-alikes
    n = len(_draft(page)["headline"])
    assert n > 0 and f"· {n} number{'s' if n != 1 else ''}" in _text(panel)
    # the name comes from the question, not the default brief's ("Leadership")
    assert _draft(page)["name"] == "Top 4 accounts buying"
    assert re.search(r'<input name="name" value="Top 4 accounts buying"', panel)
    # #95: no preview of tomorrow's brief; one line, Send beside it, the examples as chips
    assert 'class="mini-brief"' not in panel and "Tomorrow’s brief" not in panel
    assert 'class="tell ai-compose is-line"' in panel
    assert re.search(
        r'<button class="btn soft" type="submit" name="action" value="ai">Send</button>', panel
    )
    assert 'class="chip lg" type="button" data-tell-fill="Weekdays only"' in panel
    # #27 on step 2: Create says "Creating…", posts to its own action in place
    assert 'formaction="/brief/new/create" data-busy="Creating…"' in panel


def test_the_name_is_edited_in_place(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    tok = env.csrf(h + "/brief")
    q = "Are my top 4 accounts still buying?"
    d = _draft(env.client.post(h + "/brief/new", data={"csrf": tok, "question": q}).text)
    page = env.client.post(
        h + "/brief/new", data={"csrf": tok, "draft": json.dumps(d), "name": "Key accounts"}
    ).text
    assert _draft(page)["name"] == "Key accounts"
    r = env.client.post(
        h + "/brief/new/create",
        data={"csrf": tok, "draft": json.dumps(d), "name": "Top accounts"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with svc.sf() as s, tenant_scope(cid):
        assert "Top accounts" in list(s.scalars(sa.select(Brief.name)))
    # a name already taken gets a number (merged from its own test; the rule is unit-tested in
    # tests/unit/briefs/test_new_brief.py)
    r = env.client.post(
        h + "/brief/new/create", data={"csrf": tok, "draft": json.dumps(d)}, follow_redirects=False
    )
    assert r.status_code == 303
    again = _draft(env.client.post(h + "/brief/new", data={"csrf": tok, "question": q}).text)
    assert again["name"] == "Top 4 accounts buying 2"
    with svc.sf() as s, tenant_scope(cid):
        names = list(s.scalars(sa.select(Brief.name)))
    assert len(names) == len(set(names)) and "Leadership" in names


def _draft(page: str) -> dict:
    import html as _h

    return json.loads(_h.unescape(re.search(r'name="draft" value="([^"]*)"', page).group(1)))


def test_suggested_briefs_create_and_dismiss(night):  # noqa: F811
    from othor.briefs.models import SuggestedBrief

    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        for q in ("Which export customers are paying late?", "How do the plants compare?"):
            s.add(
                SuggestedBrief(
                    question=q,
                    name=q.split()[1].capitalize(),
                    draft={"headline": ["dso"], "from": ["Sweep"]},
                    evidence=["Receivables over 60 days stepped up in the week of 10 Aug"],
                )
            )
        s.commit()
        ids = list(s.scalars(sa.select(SuggestedBrief.id).order_by(SuggestedBrief.created_at)))
    page = env.client.get(h + "/brief").text
    assert "Suggested briefs · 2" in page and "Which export customers are paying late?" in page
    assert "Receivables over 60 days stepped up in the week of 10 Aug" in page
    tok = env.csrf(h + "/brief")
    env.client.post(h + f"/brief/suggested/{ids[1]}/dismiss", data={"csrf": tok})
    assert "How do the plants compare?" not in env.client.get(h + "/brief").text
    # New brief's step 1 lists it first, with where it came from; a click fills the box
    panel = _panel(env.client.get(h + "/brief/new").text)
    assert (
        f'data-tell-fill="Which export customers are paying late?" data-suggestion="{ids[0]}"'
        in panel
    )
    assert "From last night’s Sweep · Receivables over 60 days stepped up" in _text(panel)
    r = env.client.post(
        h + "/brief/new",
        data={
            "csrf": tok,
            "question": "Which export customers are paying late?",
            "suggestion": str(ids[0]),
        },
    )
    assert "Create brief" in r.text
    assert _draft(r.text)["name"] == "Export"  # the suggestion's own name, no brief has it


def test_add_to_a_brief_shows_where_it_would_rank_and_saves_a_version(night):  # noqa: F811
    from othor.briefs.models import BriefVersion
    from othor.engine.findings import pattern_of
    from othor.engine.models import Finding

    env, h, cid, rid, svc = night
    bid = _brief_id(svc, cid)
    p3 = _fp(svc, cid, rid, "concentration")  # not one of Leadership's 4 items tonight
    page = env.client.get(h + f"/sweep/finding/{p3}/brief").text
    for text in (
        "Add to a brief",
        "Leadership brief",
        "Whole company · 4 items today",
        "Tomorrow: ranks",
        "item 4 moves to “more in Sweep”",
        "Add as an item",
        "Watch it in this brief",
        "Items stay while the finding is open.",
    ):
        assert text in page, text
    tok = env.csrf(h + "/sweep")
    r = env.client.post(
        h + f"/sweep/finding/{p3}/brief",
        data={"csrf": tok, "brief": str(bid), "mode": "item"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "added=" in r.headers["location"]
    assert "Added to Leadership" in env.client.get(h + r.headers["location"]).text
    with svc.sf() as s, tenant_scope(cid):
        v2 = s.scalars(sa.select(BriefVersion).where(BriefVersion.number == 2)).one()
        f = s.scalars(
            sa.select(Finding).where(Finding.run_id == rid, Finding.fingerprint == p3)
        ).one()
        assert v2.body["pinned"] == [pattern_of(f.body)]
    again = env.client.get(h + f"/sweep/finding/{p3}/brief").text
    assert "Already added" in again
    # Watch it: the Watch panel
    r = env.client.post(
        h + f"/sweep/finding/{p3}/brief",
        data={"csrf": tok, "brief": str(bid), "mode": "watch"},
        follow_redirects=False,
    )
    assert r.headers["location"].startswith(f"/sweep/watch/{p3}")


def test_the_read_only_brief_has_no_actions(night):  # noqa: F811
    env, h, cid, rid, svc = night
    bid = _brief_id(svc, cid)
    page = env.client.get(h + f"/brief/{bid}/shared").text
    for text in (
        "Shared by",
        "read-only",
        "‹ Previous days are not shared",
        "South fell ₹1.4 Cr while West’s growth hid it",
        "Question to ask today",
        "more found last night, not in this brief",
    ):
        assert text in page, text
    for hidden in (
        "Edit brief",
        "Add to a brief",
        "Snooze",
        'href="/sweep',
        "Watching →",
        'class="nav-item',
    ):
        assert hidden not in page, hidden
    p1 = _fp(svc, cid, rid, "net_hiding_gross")
    panel = env.client.get(h + f"/brief/{bid}/shared/finding/{p1}?date=2026-11-01").text
    assert "Leadership brief · Sun 1 Nov · read-only" in panel
    for hidden in ("Snooze 7 days", "Mute", "Add a known pause", "Add to a brief"):
        assert hidden not in panel, hidden


def test_the_owners_preview_has_a_way_back(night):  # noqa: F811
    """#23: the owner's "Preview · as recipients see it" had no way back to the brief. It now has
    one; a recipient's own read-only link has none (there's nowhere for them to go back to)."""
    env, h, cid, rid, svc = night
    bid = _brief_id(svc, cid)
    page = env.client.get(h + f"/brief/{bid}/shared?date=2026-11-01").text
    head = page.split("</header>", 1)[0]
    assert "Preview · as recipients see it" in head
    assert f'href="/brief/{bid}?date=2026-11-01"' in head and "Back to the brief" in head
    p1 = _fp(svc, cid, rid, "net_hiding_gross")
    panel = env.client.get(h + f"/brief/{bid}/shared/finding/{p1}?date=2026-11-01").text
    assert "Back to the brief" in panel.split("</header>", 1)[0]


def test_a_headline_tile_opens_its_numbers_panel(night):  # noqa: F811
    """#38: a headline tile opens the same panel as that number in the Ontology, on the brief,
    in place; it closes back to the brief; the recipients' view has plain tiles."""
    env, h, cid, _rid, svc = night
    bid = _brief_id(svc, cid)
    page = env.client.get(h + f"/brief/{bid}").text
    links = re.findall(r'<a class="brief-tile is-link" href="([^"]+)"', page)
    assert links and all("panel=metric&amp;metric=" in x for x in links)
    href = html.unescape(links[0])
    key = href.rsplit("metric=", 1)[1]
    panel = env.client.get(h + href).text
    assert 'class="overlay' in panel or 'class="panel' in panel
    assert f'href="/brief/{bid}"' in panel  # its close goes back to the brief
    assert key in panel
    assert env.client.get(h + f"/brief/{bid}?panel=metric&metric=nope").status_code == 404
    shared = env.client.get(h + f"/brief/{bid}/shared").text
    assert "brief-tile is-link" not in shared


def test_the_brief_pages_description_is_behind_an_info_icon(night):  # noqa: F811
    """#45: "1 brief · first run #0002 · done" and "Made by nightly run …" moved from under the
    title into an info icon beside it."""
    env, h, *_ = night
    page = env.client.get(h + "/brief").text
    row = page[page.index('<div class="page-title">') :]
    row = row[: row.index("</div>")]
    pop = row[row.index('class="pop-body"') :]
    assert "first run #" in pop and "Made by nightly run #" in pop and "replayable" in pop
    head = page[page.index("<main") : page.index("brief-cards")]
    # #108 (owner, 21 Nov 2026): one plain line under the title; "Sent …" is behind the (i)
    assert (
        '<p class="page-lede">The few things that matter each morning, how sure Othor AI is,'
        " and who to ask.</p></div>" in head
    )


def test_snooze_from_a_brief_returns_to_it(night):  # noqa: F811
    """M15.3: Snooze and Mute on a brief's card act in place and come back to the brief, which
    then says so on the card with Undo; `back` names a brief or nothing."""
    from othor.web.routes.sweep import back_to

    env, h, cid, rid, svc = night
    bid = _brief_id(svc, cid)
    p1 = _fp(svc, cid, rid, "net_hiding_gross")
    page = env.client.get(h + f"/brief/{bid}").text
    run = re.search(rf"/sweep/finding/{p1}/snooze\?run=(\d+)", page).group(1)
    tok = env.csrf(h + f"/brief/{bid}")
    r = env.client.post(
        h + f"/sweep/finding/{p1}/snooze?run={run}&back=brief-{bid}",
        data={"csrf": tok},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == f"/brief/{bid}"
    page = env.client.get(h + f"/brief/{bid}").text
    assert "Snoozed until " in page and f"/sweep/finding/{p1}/undo?run={run}" in page
    for bad in ("https://evil.example", "brief-../x", "brief-", "sweep"):
        assert back_to(bad) is None, bad


def test_findings_come_from_areas_on_the_numbers_tab(night):  # noqa: F811
    """Board 4d4: the Numbers tab lists the Business Model's areas with their counts, each on;
    turning one off scopes the brief to the rest, saved as a version, and said on its card."""
    import html as _h

    from othor.briefs.models import BriefVersion

    env, h, cid, _rid, svc = night
    bid = _brief_id(svc, cid)
    page = env.client.get(h + f"/brief/{bid}/edit?tab=numbers").text
    assert "Findings come from" in page and 'name="areas_all" value="Collections"' in page
    areas = re.findall(r'name="areas_all" value="([^"]+)"', page)
    assert "Collections" in areas and len(areas) >= 3
    draft = re.search(r'name="draft" value="([^"]*)"', page).group(1)
    r = env.client.post(
        h + f"/brief/{bid}/edit",
        data={
            "csrf": env.csrf(h + f"/brief/{bid}"),
            "draft": _h.unescape(draft),
            "tab": "numbers",
            "action": "save",
            "areas_shown": "1",
            "areas_all": areas,
            "areas": ["Collections"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].endswith("?saved=2")
    with svc.sf() as s, tenant_scope(cid):
        v2 = s.scalars(sa.select(BriefVersion).where(BriefVersion.number == 2)).one()
        assert v2.body["scope"]["areas"] == ["Collections"]
    again = env.client.get(h + f"/brief/{bid}/edit?tab=numbers").text
    assert 'value="Collections" data-autosubmit checked' in again or (
        'aria-label="Findings from Collections" checked' in again
    )
    assert "Collections" in env.client.get(h + "/brief").text  # the card's scope line
