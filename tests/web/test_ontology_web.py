# SPDX-License-Identifier: Apache-2.0
"""The Ontology page (6a, 6a1), the metric panel (6a2, 10n), Change with Othor AI (6a3) and
History with Compare and Restore (6a4) — M10b, on the steady-state night of Sun 1 Nov 2026.
Needs `make dev && make seed`.
"""

from __future__ import annotations

import json
import re

import sqlalchemy as sa

from othor.ai.gateway import ProviderReply
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.ontology import service
from othor.ontology.models import OntologyVersion
from tests.web.conftest import Env
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _post(env: Env, h: str, path: str, data: dict | None = None):
    tok = env.csrf(h + "/ontology")
    return env.client.post(h + path, data={"csrf": tok, **(data or {})}, follow_redirects=False)


def _events(svc, cid, action):
    with svc.sf() as s, tenant_scope(cid):
        return s.scalars(sa.select(Event).where(Event.action == action)).all()


def test_the_page_has_its_four_sections(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    page = env.client.get(h + "/ontology").text
    with svc.sf() as s, tenant_scope(cid):
        v = service.current_number(s)
    # the company's name ends in a full stop; the lede doesn't double it (v0.11)
    assert f"v{v}" in page and "What Othor AI knows about Indigo Thread Co. Every brief" in page
    for text in (
        "The business",
        "Goals this year",
        "Numbers",
        "Names and words",
        "Known patterns",
        "Changes apply from the next run. Every version is kept;",
        "Chat to update",
    ):
        assert text in page, text
    # #51: a header row like the Numbers table's in the other three sections
    for head, cols in (
        ("The business", ("Line", "Source")),
        ("Names and words", ("Name", "Words", "Source")),
        ("Known patterns", ("Pattern", "Source")),
    ):
        sec = page.split(f">{head}", 1)[1].split("</section>", 1)[0]
        got = re.findall(r'<(?:th|span class="h")>([^<]+)<', sec)
        assert tuple(got[: len(cols)]) == cols, (head, got)
    # #52: the names are not bold; every section's rows are the same size (the CSS)
    names = page.split(">Names and words", 1)[1].split("</section>", 1)[0]
    assert "<b>" not in names
    # #50: what Known patterns does and its count sit behind an (i) beside the heading
    pat = page.split(">Known patterns", 1)[1].split("</div>", 1)[0]
    assert 'class="info"' in pat and "Sweep doesn’t flag these." in pat and "this month" in pat
    assert 'class="s"' not in pat and 'class="c"' not in pat
    # #49: no counts beside the business and the names, no sub-line beside Numbers
    assert "computed every night" not in page and " of 60 in use" not in page
    for head in ("The business", "Names and words"):
        sec = page.split(f">{head}", 1)[1].split("</div>", 1)[0]
        assert not re.search(r"\d+ lines?<", sec), head
    assert "tracking ₹57.2 Cr" in page  # the goal's computed projection
    # a number that needs a source is always in the short list, with Fix (6a, M12b)
    assert "Order book value" in page and "Needs a source" in page


def test_the_ceiling_note(night):  # noqa: F811
    env, h, _cid, _rid, _svc = night
    page = env.client.get(h + "/ontology?note=ceiling").text
    assert "Othor AI follows up to 60 metrics. Retire one before adding another." in page


def _main(page: str) -> str:
    return page[page.index("<h1") : page.index('id="overlay"') if 'id="overlay"' in page else None]


def test_cleaned_up_header_pill_and_tell_line(night):  # noqa: F811
    """v0.11 (docs/plans/ontology-page.md): the waiting items link to Home with the computed
    count, now the title row's button "N to review on Home" with the detail as its title (the
    page style, owner 13 Nov 2026); the foot line moved up under the lede; the Tell Othor AI
    input carries the mark and example chips that fill it."""
    from othor.home import answers
    from othor.web.home_view import proposal_rows

    env, h, cid, _rid, svc = night
    page = env.client.get(h + "/ontology").text
    with svc.sf() as s, tenant_scope(cid):
        props = len(proposal_rows(s))
        qs = len(answers.queue(s, env.clock.t.date()))
        later = len(answers.set_aside(s, env.clock.t.date()))
    assert props + qs > 0
    parts = []
    if props:
        parts.append(f"{props} proposal" + ("s" if props != 1 else ""))
    if qs:
        parts.append(f"{qs} question" + ("s" if qs != 1 else ""))
    if later:
        parts.append(f"{later} set aside")
    label = f"{props + qs + later} to review on Home"
    btn = page[page.index(label) - 300 : page.index(label) + len(label)]
    assert 'class="btn"' in btn and 'href="/home"' in btn  # the blue one is Chat to update
    assert 'title="' + " · ".join(parts) + ' waiting on Home"' in btn
    assert 'class="banner' not in _main(page)  # the full-width banner is gone
    head = page[page.index("<h1") : page.index("The business")]
    assert "Changes apply from the next run." in head  # moved up from the foot
    assert page.count("Changes apply from the next run.") == 1
    # owner, 18 Nov 2026: the Tell Othor AI line went; "Chat to update" by the title opens the
    # one chat on the Business Model
    assert 'class="tell"' not in page
    assert (
        'class="btn primary" href="/ontology?panel=othor&amp;subject=ontology" data-panel>'
        "Chat to update" in page
    )


def test_numbers_section_latest_see_all_and_fix(night):  # noqa: F811
    env, h, *_ = night
    page = env.client.get(h + "/ontology").text
    nums = page[page.index('aria-label="Numbers"') : page.index('aria-label="Names and words"')]
    assert ">Latest<" in nums and ">Today<" not in nums
    assert "In briefs" not in nums  # the column goes; the metric panel still says where
    # "16 numbers · See all →" in the section head; the bottom link goes
    assert " numbers" in nums and 'href="/ontology?numbers=all"' in nums and "See all →" in nums
    assert "All " not in nums or " numbers →" not in nums
    # no bullet dot on every row
    assert 'class="dot' not in nums
    # Change is in the row (shown on hover/focus by CSS); Fix on a needs-a-source row always
    needs_row = nums[nums.index("Order book value") - 600 : nums.index("Order book value") + 1200]
    assert 'class="needs"' in needs_row
    fix = needs_row[needs_row.rindex("<a ", 0, needs_row.index(">Fix</a>")) :]
    assert fix.startswith('<a class="link" href=')  # not .ont-act: always visible
    assert (
        '<a class="link ont-act" href="/ontology?panel=othor&amp;subject=ontology:metric:' in nums
    )
    # elsewhere on the page: no per-row dots either
    for section in ("The business", "Names and words"):
        part = page[page.index(f'aria-label="{section}"') :]
        part = part[: part.index("</section>")]
        assert 'class="dot' not in part, section


def test_a_number_without_a_value_says_no_data_yet(night, monkeypatch):  # noqa: F811
    from othor.web import ontology_view

    env, h, *_ = night
    real = ontology_view.tile_for
    monkeypatch.setattr(
        ontology_view, "tile_for", lambda rd, key: None if key == "net_revenue" else real(rd, key)
    )
    page = env.client.get(h + "/ontology").text
    nums = page[page.index('aria-label="Numbers"') : page.index('aria-label="Names and words"')]
    row = nums[nums.index("Net revenue") : nums.index("</tr>", nums.index("Net revenue"))]
    assert "No data yet" in row


def test_names_and_words_is_a_three_column_table(night):  # noqa: F811
    env, h, *_ = night
    page = env.client.get(h + "/ontology").text
    names = page[page.index('aria-label="Names and words"') :]
    names = names[: names.index("</section>")]
    assert "<table" in names and ">Key customers</td>" in names
    assert "Key customers:" not in names  # the term is its own column, no colon
    assert names.count("<tr") >= 2 and names.count('class="src"') >= 2


def test_known_patterns_empty_state(night, monkeypatch):  # noqa: F811
    from othor.web import ontology_view

    env, h, *_ = night
    page = env.client.get(h + "/ontology").text
    known = page[page.index('aria-label="Known patterns"') :]
    assert "suppressed" in known[: known.index("</section>")]  # with patterns: the count
    monkeypatch.setattr(ontology_view, "pattern_lines", lambda s: [])
    page = env.client.get(h + "/ontology").text
    known = page[page.index('aria-label="Known patterns"') :]
    known = known[: known.index("</section>")]
    assert "None yet" in known and "Sweep hasn’t learned any patterns yet." in known
    assert "suppressed" not in known


def test_all_numbers_with_filters(night):  # noqa: F811
    env, h, *_ = night
    page = env.client.get(h + "/ontology?numbers=all").text
    assert "Confirmed " in page and "Need a source " in page and "Not in a brief " in page
    assert "+ Add a number" in page
    needs = env.client.get(h + "/ontology?numbers=needs").text
    assert "Fix" in needs


def test_the_metric_panel(night):  # noqa: F811
    env, h, *_ = night
    p = env.client.get(h + "/ontology?metric=net_revenue").text
    for text in (
        "Net revenue",
        "Confirmed",
        "Definition",
        "SQL",
        "Generated from the definition, not written by AI",
        "-- net_revenue · month · spec v",
        "Copy",
        "Used in",
        "Leadership",
        "Change with Othor AI",
        "Add to a brief",
        "<svg",
    ):
        assert text in p, text
    assert env.client.get(h + "/ontology?metric=nope").status_code == 404


def test_history_compare_restore_and_no_change(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        n = service.current_number(s)
        voc = next(r for r in service.active(s, ["vocabulary"]) if r.source_ref)
        service.apply(
            s,
            [service.put("vocabulary", voc.key, {**voc.body, "text": "edited for the test"})],
            actor="o",
            reason="An edit",
        )
        s.commit()
    page = env.client.get(h + "/ontology?panel=history").text
    assert "Every change is a version. Briefs record the version they used." in page
    assert "Restore makes a new version; nothing is deleted" in page
    cmp_ = env.client.get(h + f"/ontology?panel=history&compare={n}-{n + 1}").text
    assert f"Compare v{n} and v{n + 1}" in cmp_ and "edited for the test" in cmp_
    ask = env.client.get(h + f"/ontology?panel=history&restore={n}").text
    assert f"Restore v{n} as v{n + 2}? It applies from tonight’s run. Nothing is deleted." in ask
    r = _post(env, h, f"/ontology/versions/{n}/restore")
    assert r.headers["location"] == f"/ontology?panel=history&done={n + 2}"
    done = env.client.get(h + r.headers["location"]).text
    assert f"Restored as v{n + 2}" in done and f"same content as v{n}" in done
    with svc.sf() as s, tenant_scope(cid):
        a, b = (
            s.scalars(sa.select(OntologyVersion).where(OntologyVersion.number == x)).one()
            for x in (n, n + 2)
        )
        assert a.content_hash == b.content_hash
    # restoring the current content again changes nothing
    r = _post(env, h, f"/ontology/versions/{n}/restore")
    assert "note=nochange" in r.headers["location"]
    assert "No change" in env.client.get(h + r.headers["location"]).text
    assert _events(svc, cid, "ontology.restored")


def test_change_with_othor_ai_without_a_model(night):  # noqa: F811
    """0.26 (M26.4): Change with Othor AI opens the one chat on the number; without a model it
    says so and nothing is drafted."""
    env, h, *_ = night
    page = env.client.get(h + "/ontology?panel=othor&subject=ontology:metric:net_revenue").text
    chat = page.split('id="othor-chat"', 1)[1]
    assert "Change Net revenue" in chat and "What should change about this number?" in chat
    assert "Drafts only" not in chat or "Nothing changes until you apply" in chat
    back = "/ontology?panel=othor&subject=ontology:metric:net_revenue"
    _post(
        env,
        h,
        "/othor/reply",
        {
            "text": "Kochi starts in December",
            "subject": "ontology:metric:net_revenue",
            "back": back,
        },
    )
    page = env.client.get(h + back).text
    assert "draft changes right now" in page and "Apply 1" not in page


def test_another_companys_objects_are_404(night):  # noqa: F811
    env, h, *_ = night
    assert _post(env, h, "/ontology/versions/9999/restore").status_code == 404
    r = _post(env, h, "/othor/reply", {"text": "x", "subject": "ontology:metric:nope"})
    assert r.status_code == 303  # a number that isn't there: nothing drafted
    page = env.client.get(h + "/ontology?panel=othor&subject=ontology:metric:nope").text
    assert "x</p>" not in page.split('id="othor-chat"', 1)[-1]
    assert env.client.get(h + "/ontology?panel=history&compare=1-9999").status_code == 404


def test_a_draft_shows_its_effect_and_todays_values_then_apply_and_undo(night, monkeypatch):  # noqa: F811
    """0.26 (M26.4): in the one chat a drafted change shows its effect and, read live, today's
    values before and after (never stored); Apply writes one version once; Undo still works."""
    from othor.connectors.models import RunQuery
    from othor.setup.models import ChatMessage
    from othor.web.routes import othor_chat as routes

    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        n = service.current_number(s)
        versions = s.scalar(sa.select(sa.func.count()).select_from(OntologyVersion))

    class Fake:
        name, configured = "fake", True

        def call(self, system, messages, schema, max_tokens=0):
            out = {
                "changes": [
                    {
                        "op": "put",
                        "type": "metric",
                        "key": "receivables_open",
                        "body_json": json.dumps({"breakdowns": ["customer"]}),
                    }
                ],
                "reply": "Open receivables will be split by customer only.",
                "clarify": None,
            }
            return ProviderReply(json.dumps(out), "fake", 1, 1)

    monkeypatch.setattr(routes, "provider_for", lambda settings: Fake())
    subject = "ontology:metric:receivables_open"
    back = f"/ontology?panel=othor&subject={subject}"
    _post(
        env,
        h,
        "/othor/reply",
        {"text": "split open receivables by customer only", "subject": subject, "back": back},
    )
    page = env.client.get(h + back).text
    chat = page.split('id="othor-chat"', 1)[1]
    assert "Open receivables will be split by customer only." in chat
    assert "change meaning:" in chat and "Open receivables" in chat and "Apply 1 change" in chat
    m = re.search(r'hx-get="(/othor/preview\?[^"]+)"', chat)
    assert m, "today's values load as the chat opens"
    vals = env.client.get(h + m.group(1).replace("&amp;", "&"))
    assert "Open receivables today:" in vals.text and vals.headers["cache-control"] == "no-store"
    with svc.sf() as s, tenant_scope(cid):
        stored = json.dumps([x.proposal for x in s.scalars(sa.select(ChatMessage))])
        assert "today:" not in stored  # never stored as a value
        assert s.scalars(sa.select(RunQuery).where(RunQuery.purpose == "preview")).first()
        assert service.current_number(s) == n  # nothing before Apply
    _post(env, h, "/othor/apply", {"subject": subject, "back": back})
    _post(env, h, "/othor/apply", {"subject": subject, "back": back})  # once
    assert f"as Business Model v{n + 1}" in env.client.get(h + back).text
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalar(sa.select(sa.func.count()).select_from(OntologyVersion)) == versions + 1
        rec = next(r for r in service.active(s, ["metric"]) if r.key == "receivables_open")
        assert rec.body["breakdowns"] == ["customer"]
    _post(env, h, f"/ontology/versions/{n + 1}/undo", {"back": "/ontology"})
    with svc.sf() as s, tenant_scope(cid):
        assert service.current_number(s) == n + 2
        rec = next(r for r in service.active(s, ["metric"]) if r.key == "receivables_open")
        assert rec.body["breakdowns"] != ["customer"]


def test_the_page_description_is_behind_an_info_icon(night):  # noqa: F811
    """#43: "What Othor AI knows about …" and "Changes apply from the next run …" moved from under
    the title into an info icon beside it."""
    env, h, *_ = night
    page = env.client.get(h + "/ontology").text
    row = page[page.index('<div class="page-title">') :]
    row = row[: row.index("</div>")]
    pop = row[row.index('class="pop-body"') :]
    assert "What Othor AI knows about" in pop and "Changes apply from the next run." in pop
    assert 'aria-label="About this page"' in row
    assert 'class="page-lede"' not in page[page.index("<main") : page.index("The business")]


def test_each_section_heading_has_an_icon_and_no_number(night):  # noqa: F811
    """#42: THE BUSINESS, NUMBERS, NAMES AND WORDS and KNOWN PATTERNS carry an icon each; the
    1, 2, 3, 4 numbering is gone."""
    import re

    env, h, *_ = night
    page = env.client.get(h + "/ontology").text
    heads = re.findall(r'<div class="ont-head"><p class="t">(.*?)</p>', page)
    assert len(heads) >= 4, heads
    for t in heads:
        assert '<svg class="icon' in t, t
        assert not re.search(r'class="n">\s*\d', t), t
