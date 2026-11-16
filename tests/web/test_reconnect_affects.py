# SPDX-License-Identifier: Apache-2.0
"""10m Reconnect · "What it affects" (M12b): the numbers a disconnected source feeds, each with
its value as of the last good read and the briefs it's in, never the "nothing yet" empty state
while Home (10k) says those numbers are stale. Needs `make dev && make seed`."""

from __future__ import annotations

from tests.fixtures import board_states as bs
from tests.web.board_worlds import apply, night_world  # noqa: F401
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def test_reconnect_lists_what_the_source_feeds(night_world) -> None:  # noqa: F811
    w = night_world
    st = bs.STATES["10m"]
    values = apply(st, w)
    html = w.env.client.get(w.host + bs.fill(st.path, values)).text
    assert "What it affects" in html
    assert "No numbers or briefs use this source yet." not in html
    for col in ("Number", "Last good value", "Used in"):
        assert f"<th>{col}</th>" in html, col
    home = w.env.client.get(w.host + "/home").text
    assert "Net revenue (MIS)" in home and "Net revenue (MIS)" in html


def test_the_brief_names_the_stale_number_and_where_to_reconnect(night_world) -> None:  # noqa: F811
    """10l: the stale number needn't be a tile; the banner still names it and its source."""
    w = night_world
    st = bs.STATES["10l"]
    values = apply(st, w)
    html = w.env.client.get(w.host + bs.fill(st.path, values)).text
    assert "1 number from a stale source" in html
    assert "Net revenue (MIS) is from" in html
    assert "MIS-Weekly couldn’t be read last night" in html
    assert "/sources?panel=reconnect&amp;s=" in html or "/sources?panel=reconnect&s=" in html


def test_sources_say_what_each_source_feeds(night_world) -> None:  # noqa: F811
    """6c, 6d, 10r (M12b): Feeds, the Manage stat and Remove's impact come from the metrics a
    source feeds and the briefs that show them, never "nothing uses this" on a source in use."""
    w = night_world
    sid = w.values["sid"]
    page = w.env.client.get(w.host + "/sources").text
    assert " metrics</td>" in page or " metrics\n" in page or "metrics</td>" in page
    manage = w.env.client.get(w.host + f"/sources?panel=manage&s={sid}").text
    assert "No numbers yet" not in manage and "Not connected yet" not in manage
    assert "computed from it" in manage
    detail = w.env.client.get(w.host + f"/sources/{sid}").text
    assert "Nothing uses this source yet" not in detail and "Removing it stops" in detail


def test_the_disconnect_question_is_in_the_footer_where_it_was_asked(night_world) -> None:  # noqa: F811
    """#25: the question sat at the end of the panel's body, below the fold, so Disconnect…
    seemed to do nothing. It now replaces the footer's links, always on screen, and says what
    the source feeds instead of "nothing uses this" on a source in use."""
    w = night_world
    sid = w.values["sid"]
    html = w.env.client.get(w.host + f"/sources?panel=manage&s={sid}&confirm=disconnect").text
    body, foot = html.split('<div class="pane-body">', 1)[1].split('<footer class="pane-foot">', 1)
    assert 'role="alertdialog"' not in body
    assert 'role="alertdialog"' in foot and "Keep it" in foot
    assert "Pause reading" not in foot.split("</footer>", 1)[0]
    assert "will need a source" in foot and "No numbers or briefs use this source yet" not in foot
