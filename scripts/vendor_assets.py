# SPDX-License-Identifier: Apache-2.0
"""Download the self-hosted web assets (fonts, htmx, the Lucide icon sprite) once and pin them
by SHA-256.

No page or email loads anything from a CDN (on-premise rule). Run this only to change a
version; the files and MANIFEST.json are committed.

    uv run python scripts/vendor_assets.py                 # everything
    uv run python scripts/vendor_assets.py --only icons    # one part (fonts, icons, htmx, alpine)

Each part merges into MANIFEST.json; parts not run are left as they are.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

STATIC = Path(__file__).resolve().parents[1] / "othor" / "web" / "static"
HTMX = "https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js"
# The CSP build: no eval/new Function, so `script-src 'self'` holds (M12). MIT licence.
ALPINE = "https://unpkg.com/@alpinejs/csp@3.14.9/dist/cdn.min.js"
FAMILIES = "family=Inter:wght@400;500;600;700&family=Geist+Mono:wght@400;500"
# The public website's extra faces (owner's design, 10 Nov 2026): kept apart from the app's, so
# fetching them never renames or re-pins the fonts the app and the chart PNGs use.
# Inter Light (the app serves 400-700, the same shapes the design was drawn in) and JetBrains Mono.
SITE_FAMILIES = "family=Inter:wght@300&family=JetBrains+Mono:wght@400;500"
LUCIDE = "https://unpkg.com/lucide-static@0.469.0/icons/{}.svg"  # ISC licence
# our icon name → Lucide icon. Templates call icon("name"); add a line here, then --only icons.
ICONS = {
    "home": "house",
    "dna": "dna",  # the website's hero pill (owner, 10 Nov 2026)
    "brief": "newspaper",
    "sweep": "activity",
    "ontology": "book-open-text",
    "sources": "database",
    "settings": "settings",
    "search": "search",
    "upload": "upload",
    "download": "download",
    "close": "x",
    "chevron-down": "chevron-down",
    "chevron-right": "chevron-right",
    "chevron-left": "chevron-left",
    "check": "check",
    "plus": "plus",
    "arrow-right": "arrow-right",
    "arrow-left": "arrow-left",
    "warning": "triangle-alert",
    "info": "info",
    "help": "circle-help",
    "copy": "copy",
    "external": "external-link",
    "more": "ellipsis",
    "refresh": "refresh-cw",
    "delete": "trash-2",
    "edit": "pencil",
    "filter": "list-filter",
    "calendar": "calendar",
    "mail": "mail",
    "lock": "lock",
    "sign-out": "log-out",
    "user": "user",
    "usage": "gauge",  # AI usage in the side menu (M15.8)
    "summary": "layout-list",  # Summary in the side menu (M16.3)
    "file": "file-text",
    "sheet": "sheet",
    "table": "table-2",
    "menu": "menu",
    "eye": "eye",
    "eye-off": "eye-off",
    "clock": "clock",
    "chat": "message-square",
    "send": "send",
    "link": "link",
    "undo": "undo-2",
    "company": "building-2",
    "bell": "bell",
    "ok": "circle-check",
    "error": "circle-x",
    "loading": "loader-circle",
    # Home's lights (owner, 16 Nov 2026): one icon per light
    "plug": "plug",
    "columns": "columns-3",
    "volume": "chart-column",
    "run": "moon",
    "numbers": "calculator",
    "findings": "scan-search",
    "forecast": "trending-up",
    "checked": "shield-check",
    "next": "calendar-clock",
}
MODERN_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
)


def get(url: str, ua: str = "othor-vendor") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=60) as r:
        return bytes(r.read())


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--only", choices=["htmx", "alpine", "fonts", "site-fonts", "icons"], action="append"
    )
    parts = set(ap.parse_args().only or ["htmx", "alpine", "fonts", "icons"])
    path = STATIC / "MANIFEST.json"
    manifest: dict[str, dict[str, str]] = json.loads(path.read_text()) if path.exists() else {}

    def save(rel: str, data: bytes, src: str) -> None:
        file = STATIC / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        manifest[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "source": src}

    if "htmx" in parts:
        save("js/htmx.min.js", get(HTMX), HTMX)
    if "alpine" in parts:
        save("js/alpine-csp.min.js", get(ALPINE), ALPINE)
    if "fonts" in parts:
        fonts(save)
    if "site-fonts" in parts:
        _site_fonts()
    if "icons" in parts:
        icons(save)
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(f"{len(manifest)} files")


def _site_fonts() -> None:
    """The website's fonts go into the private website plug-in (M20.2): othor_site/static, its
    own MANIFEST.json, served at the same URLs (/static/fonts/site/…, /static/site/…)."""
    static = STATIC.parents[2] / "othor_site" / "static"
    path = static / "MANIFEST.json"
    manifest: dict[str, dict[str, str]] = json.loads(path.read_text()) if path.exists() else {}

    def save(rel: str, data: bytes, src: str) -> None:
        rel = "site/fonts-site.css" if rel == "css/fonts-site.css" else rel
        file = static / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        manifest[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "source": src}

    site_fonts(save)
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")


def fonts(save: Any) -> None:
    # Web fonts: woff2 per unicode range, as Google serves them to modern browsers.
    css = get(f"https://fonts.googleapis.com/css2?{FAMILIES}&display=swap", MODERN_UA).decode()
    out = []
    for i, block in enumerate(re.findall(r"/\* [^*]+ \*/\s*@font-face \{[^}]+\}", css)):
        url = re.search(r"url\((https://[^)]+)\)", block)
        assert url
        family = re.search(r"font-family: '([^']+)'", block)
        weight = re.search(r"font-weight: (\d+)", block)
        assert family and weight
        rel = f"fonts/{family.group(1).replace(' ', '')}-{weight.group(1)}-{i:02d}.woff2"
        save(rel, get(url.group(1)), url.group(1))
        out.append(block.replace(url.group(1), f"/static/{rel}"))
    fonts_css = (
        "/* Self-hosted Inter and Geist Mono (SIL OFL 1.1). "
        "Generated by scripts/vendor_assets.py */\n"
    )
    save("css/fonts.css", (fonts_css + "\n".join(out) + "\n").encode(), "generated")
    # TTF for the PNG renderer (resvg reads TTF/OTF, not woff2): old UA gets full TTF files.
    css_ttf = get(f"https://fonts.googleapis.com/css2?{FAMILIES}").decode()
    for block in re.findall(r"@font-face \{[^}]+\}", css_ttf):
        url = re.search(r"url\((https://[^)]+\.ttf)\)", block)
        family = re.search(r"font-family: '([^']+)'", block)
        weight = re.search(r"font-weight: (\d+)", block)
        if url and family and weight:
            rel = f"fonts/ttf/{family.group(1).replace(' ', '')}-{weight.group(1)}.ttf"
            save(rel, get(url.group(1)), url.group(1))


def site_fonts(save: Any) -> None:
    """The website's Inter Light and JetBrains Mono: woff2 only (no chart uses them)."""
    css = get(f"https://fonts.googleapis.com/css2?{SITE_FAMILIES}&display=swap", MODERN_UA)
    out = []
    for i, block in enumerate(re.findall(r"/\* [^*]+ \*/\s*@font-face \{[^}]+\}", css.decode())):
        url = re.search(r"url\((https://[^)]+)\)", block)
        family = re.search(r"font-family: '([^']+)'", block)
        weight = re.search(r"font-weight: ([\d ]+);", block)
        assert url and family and weight
        name = family.group(1).replace(" ", "")
        rel = f"fonts/site/{name}-{weight.group(1).replace(' ', '-')}-{i:02d}.woff2"
        save(rel, get(url.group(1)), url.group(1))
        block = block.replace(url.group(1), f"/static/{rel}")
        out.append(block)
    head = "/* The website's Inter 300 and JetBrains Mono (SIL OFL 1.1). By scripts/vendor_assets.py */\n"
    save("css/fonts-site.css", (head + "\n".join(out) + "\n").encode(), "generated")


def icons(save: Any) -> None:
    """One SVG sprite: a <symbol> per icon, geometry only (stroke and size come from CSS)."""
    symbols = []
    for name, lucide in sorted(ICONS.items()):
        svg = get(LUCIDE.format(lucide)).decode()
        inner = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S)
        assert inner, lucide
        body = re.sub(r"\s+", " ", re.sub(r"<!--.*?-->", "", inner.group(1), flags=re.S)).strip()
        symbols.append(f'<symbol id="{name}" viewBox="0 0 24 24">{body}</symbol>')
    sprite = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        "<!-- Lucide icons (ISC), lucide-static@0.469.0, by scripts/vendor_assets.py -->"
        + "".join(symbols)
        + "</svg>\n"
    )
    save("icons/sprite.svg", sprite.encode(), LUCIDE.format("*"))


if __name__ == "__main__":
    main()
