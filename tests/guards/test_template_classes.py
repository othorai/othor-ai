# SPDX-License-Identifier: Apache-2.0
"""Page templates use layout utilities and macros only (UI foundation, decision 3). Areas listed
in scripts/check_template_classes.py STRICT fail CI on any finding; @apply only in components."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "check_template_classes", ROOT / "scripts" / "check_template_classes.py"
)
assert spec and spec.loader
ctc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctc)


def test_strict_areas_have_no_visual_classes():
    bad = [
        f"{p.relative_to(ROOT)}:{line}: {tok}"
        for p in ctc.page_templates()
        if ctc.strict_area(p)
        for line, tok in ctc.findings(p)
    ]
    assert bad == [], "\n".join(bad)


def test_the_allowlist():
    ok = [
        "flex",
        "grid",
        "gap-12",
        "mt-24",
        "md:grid-cols-2",
        "w-full",
        "max-w-1176",
        "sticky",
        "top-0",
        "order-2",
        "overflow-auto",
        "js-copy",
        "-mt-4",
        "lg:hidden",
    ]
    no = [
        "text-13",
        "bg-accent",
        "border",
        "rounded-card",
        "shadow-panel",
        "opacity-50",
        "transition",
        "font-semibold",
        "w-[13px]",
        "hint",
        "btn",
    ]
    assert all(ctc.allowed(t) for t in ok), [t for t in ok if not ctc.allowed(t)]
    assert not any(ctc.allowed(t) for t in no), [t for t in no if ctc.allowed(t)]


def test_apply_only_in_components():
    styles = ROOT / "othor" / "web" / "styles"
    for p in styles.rglob("*.css"):
        if "@apply" in p.read_text():
            assert p.name.startswith("components"), f"@apply outside components: {p}"
    for p in (ROOT / "othor" / "web" / "templates").rglob("*.html"):
        rel = p.relative_to(ROOT / "othor" / "web" / "templates")
        if rel.parts[0] != "components":
            assert "@apply" not in p.read_text(), f"@apply in a template: {p}"


def test_ui_refuses_unknown_components_and_visual_layout():
    import pytest

    from othor.web.ui import components, ui

    assert {"btn", "card", "hint", "chip"} <= components()
    assert ui("card flush", "mt-16 md:grid-cols-2") == "card flush mt-16 md:grid-cols-2"
    assert ui("chip is-on") == "chip is-on"
    with pytest.raises(ValueError, match="not a component"):
        ui("bg-accent")
    with pytest.raises(ValueError, match="not a layout"):
        ui("card", "text-13")
    with pytest.raises(ValueError, match="not a layout"):
        ui("card", "w-[13px]")


def test_legacy_stylesheet_is_gone():
    assert not (ROOT / "othor" / "web" / "static" / "css" / "othor.css").exists()


def test_alpine_attributes_live_in_components_only(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text(
        '<div x-data="menu">\n<button @click="toggle" :aria-expanded="open">x</button></div>'
    )
    toks = [tok for _line, tok in ctc.findings(page)]
    assert "x-data" in toks and "@click" in toks and ":aria-expanded" in toks
