# SPDX-License-Identifier: Apache-2.0
"""Settings in tabs (#106, owner 21 Nov 2026): Company first, then the rest in their order; one
pane shown at a time, the others in the page but hidden (switched in place by othor.js); the
address's ?tab= opens its tab, an unknown one opens Company; a save stays on its tab."""

from __future__ import annotations

import re
from pathlib import Path

from tests.web.conftest import make_env, signup


def _tabs(page: str) -> list[str]:
    bar = re.search(r"<nav[^>]*data-pane-tabs[^>]*>(.*?)</nav>", page, re.S)
    assert bar, "the tab bar"
    return re.findall(r'data-tab="([a-z]+)"', bar.group(1))


def _shown(page: str) -> list[str]:
    return re.findall(r'data-pane="([a-z]+)">', page)


def test_the_tabs_open_on_company_and_follow_the_address(tmp_path: Path) -> None:
    with make_env(tmp_path, signup="open") as env:
        signup(env)
        h = env.host("indigothread")
        page = env.client.get(h + "/settings").text
        # Licence last when the install runs several companies (as here)
        assert _tabs(page) == ["company", "ai", "charts", "delivery", "data", "licence"]
        assert _shown(page) == ["company"]
        # every pane is in the page, so a tab shows without asking the server again
        for key in ("company", "ai", "charts", "delivery", "data", "licence"):
            assert f'data-pane="{key}"' in page
        assert 'href="?tab=charts"' in page
        charts = env.client.get(h + "/settings?tab=charts").text
        assert _shown(charts) == ["charts"]
        assert re.search(r'class="tab is-on"[^>]*data-tab="charts"', charts)
        # Delete company sits under Your data, beside the export it points to
        data = env.client.get(h + "/settings?tab=data").text
        pane = data.split('data-pane="data"', 1)[1].split("data-pane=", 1)[0]
        assert "export-row" in pane and "Delete the company" in pane
        assert _shown(env.client.get(h + "/settings?tab=nothing").text) == ["company"]


def test_a_save_stays_on_its_tab(tmp_path: Path) -> None:
    """A save swaps only its toast (no page comes back), so the open tab stays; Undo reloads the
    address it was on, ?tab= included."""
    with make_env(tmp_path, signup="open") as env:
        signup(env)
        h = env.host("indigothread")
        tok = env.csrf(h + "/settings?tab=charts")
        r = env.client.post(
            h + "/settings",
            data={"csrf": tok, "field": "chart_values", "value": "off"},
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200 and "Values on lines saved" in r.text
        assert "data-pane-tabs" not in r.text  # the toast alone
