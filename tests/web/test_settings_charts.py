# SPDX-License-Identifier: Apache-2.0
"""Settings › Charts (v0.25 M25.1, owner 18 Nov 2026): smooth lines on by default, values on lines
Key points by default; each saved in place with an event, and a page's charts follow at once."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from othor.core.models import Company, Event
from othor.core.tenancy import system_scope
from tests.web.conftest import make_env, signup


def test_the_charts_section_saves_in_place(tmp_path: Path) -> None:
    with make_env(tmp_path, signup="open") as env:
        signup(env)
        h = env.host("indigothread")
        page = env.client.get(h + "/settings").text
        assert 'aria-label="Charts"' in page and "Smooth lines" in page
        assert "Values on lines" in page and 'value="key" selected' in page
        tok = env.csrf(h + "/settings")
        r = env.client.post(
            h + "/settings", data={"csrf": tok, "field": "chart_smooth", "value": ""}
        )
        assert r.status_code == 204  # a switch shows its own state
        r = env.client.post(
            h + "/settings", data={"csrf": tok, "field": "chart_values", "value": "off"}
        )
        assert r.status_code == 200 and "Values on lines saved" in r.text
        bad = env.client.post(
            h + "/settings", data={"csrf": tok, "field": "chart_values", "value": "all"}
        )
        assert bad.status_code == 400
        svc = env.app.state.services  # type: ignore[attr-defined]
        with svc.sf() as s, system_scope("test"):
            co = s.scalars(sa.select(Company).where(Company.slug == "indigothread")).one()
            assert co.chart_smooth is False and co.chart_values == "off"
            changed = [
                e.after
                for e in s.scalars(sa.select(Event).where(Event.action == "settings.changed"))
            ]
        assert {"chart_smooth": False} in changed and {"chart_values": "off"} in changed
