# SPDX-License-Identifier: Apache-2.0
"""One source for colours: charts read othor/web/styles/theme.css, the file the pages use."""

from __future__ import annotations

from othor.charts import svg
from othor.charts.tokens import THEME, color, tokens


def test_chart_roles_come_from_the_theme():
    assert {
        "positive": color("accent"),
        "negative": color("neg"),
        "neutral": color("ink"),
        "muted": color("muted-2"),
        "edge": color("ink"),  # just outside its band, with a ring (M15.3)
    } == svg.ROLE
    assert color("accent") == "#2CCAFF" and color("neg") == "#D64545"


def test_theme_has_board_tokens_only():
    t = tokens()
    assert "--*: initial;" in THEME.read_text()
    assert t["spacing"] == "1px"
    assert {"text-meta", "text-row", "radius-8", "shadow-panel", "font-sans"} <= set(t)
    # green is for status dots only: one ok family, no data greens
    greens = {
        k
        for k, v in t.items()
        if k.startswith("color-")
        and v.upper() in ("#1B8A4A", "#1E5A36", "#1E7F4F", "#EEF8F1", "#C9E6D2")
    }
    assert all(k.startswith("color-ok") for k in greens)
