# SPDX-License-Identifier: Apache-2.0
"""The company's tab icon (Company identity 1.0, owner 24 Nov 2026): each company's subdomain
shows its own icon in the browser tab, so an owner with several companies open tells them apart
at a glance. othor.ai and the sign-in pages keep the Othor AI icon.

- With a logo: the logo's mark, its empty margins trimmed, made into 16, 32 and 180 px PNGs
  (the favicon and the phone's home-screen icon), written next to the logo when it is uploaded.
- With no logo, or when the owner chose the letters: the first two letters of the company's name,
  cyan on the dark slate of the Othor style (theme.css), drawn by code with the bundled Inter.
- A logo that won't read at tab size (too wide, too faint or too detailed) is found by simple
  image rules (no model); Settings then suggests the letters, a choice the owner applies.

Every icon's URL carries a version made from what it shows (the logo's hash, or the letters and
the drawing's style), so a new logo or a rename shows at once while each URL caches for a year.
Same inputs, same bytes: Pillow is pinned and nothing here is random.
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from othor.charts.tokens import color
from othor.core.config import Settings

if TYPE_CHECKING:
    from PIL import Image as PILImage

SIZES = (16, 32, 180)
STYLE = 1  # the letters' drawing: bump it when the look changes, and every letters URL changes
FONTS = Path(__file__).resolve().parent / "static" / "fonts" / "ttf"
VERSION = re.compile(r"^[0-9a-f]{16}$")
# letters Inter draws: Latin (with its accents), Greek and Cyrillic; a name in another script
# keeps the Othor AI icon (there is no font for it in the image)
_DRAWABLE = (
    (0x30, 0x39),
    (0x41, 0x5A),
    (0x61, 0x7A),
    (0xC0, 0x24F),
    (0x370, 0x3FF),
    (0x400, 0x4FF),
)
_WORD = re.compile(r"[^\W_]+")

# what the owner reads when the logo won't do at tab size (Settings › Company › Logo)
REASONS = {
    "wide": "too wide to read in a browser tab",
    "faint": "too light to see in a browser tab",
    "busy": "too detailed to read in a browser tab",
}


def _drawable(ch: str) -> bool:
    return any(lo <= ord(ch) <= hi for lo, hi in _DRAWABLE)


def letters(name: str) -> str:
    """The first letter of each of the first two words ("Indigo Thread Co." → "IT"); one word
    gives its first two letters ("Acme" → "AC"). Empty when nothing in the name can be drawn."""
    words = ["".join(c for c in w if _drawable(c)) for w in _WORD.findall(name or "")]
    words = [w for w in words if w]
    if not words:
        return ""
    picked = words[0][0] + words[1][0] if len(words) > 1 else words[0][:2]
    return picked.upper()


# ---------------------------------------------------------------------------------------------
# Which icon a company shows, and its URLs


@dataclass(frozen=True)
class Icon:
    kind: str  # logo | letters
    version: str  # 16 hex: in the URL and the ETag
    text: str = ""  # the letters, for kind letters
    logo_sha: str | None = None

    def url(self, size: int) -> str:
        return f"/tab-icon/{self.version}-{size}.png"


def _version(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def choose(company: Any) -> Icon | None:
    """The icon the company's tab shows: its logo, unless it has none or the owner chose the
    letters; None (the Othor AI icon) when the name has nothing to draw."""
    sha = getattr(company, "logo_sha", None)
    if sha and getattr(company, "tab_icon", None) != "letters":
        return Icon("logo", _version("logo", sha), logo_sha=sha)
    text = letters(getattr(company, "name", "") or "")
    if not text:
        return None
    return Icon("letters", _version("letters", STYLE, text), text=text)


def links(company: Any) -> dict[str, str] | None:
    """The three links for the page's head, or None for the Othor AI icon."""
    icon = choose(company)
    if icon is None:
        return None
    return {"16": icon.url(16), "32": icon.url(32), "180": icon.url(180)}


# ---------------------------------------------------------------------------------------------
# Drawing the letters


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@lru_cache(maxsize=256)
def letters_png(text: str, size: int) -> bytes:
    """Two letters, cyan on slate, in a rounded square (a full square at 180 px: phones round
    it themselves). Drawn 4× and scaled down, so the edges are smooth at 16 px."""
    from PIL import Image, ImageDraw, ImageFont

    if size not in SIZES:
        raise ValueError(size)
    k = 4
    big = size * k
    bg, fg = _rgb(color("slate")), _rgb(color("accent"))
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    radius = 0 if size >= 180 else round(big * 0.22)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=radius, fill=(*bg, 255))
    # the letters fill more of a 16 px square: every pixel counts there
    fill_w = {16: 0.84, 32: 0.74, 180: 0.62}[size]
    cap_h = {16: 0.60, 32: 0.52, 180: 0.44}[size]
    weight = "Inter-700.ttf" if size < 180 else "Inter-600.ttf"
    pt = big
    font = ImageFont.truetype(str(FONTS / weight), pt)
    for _ in range(3):  # fit width and height; two passes settle it
        x0, y0, x1, y1 = font.getbbox(text)
        scale = min(fill_w * big / max(x1 - x0, 1), cap_h * big / max(y1 - y0, 1))
        pt = max(4, int(pt * scale))
        font = ImageFont.truetype(str(FONTS / weight), pt)
    x0, y0, x1, y1 = font.getbbox(text)
    d.text(((big - (x1 - x0)) / 2 - x0, (big - (y1 - y0)) / 2 - y0), text, font=font, fill=fg)
    out = im.resize((size, size), Image.Resampling.LANCZOS)
    if size >= 180:  # a home-screen icon has no clear corners: iOS would fill them black
        out = out.convert("RGB")
    return _png(out)


def _png(im: PILImage.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------
# From the logo


def _open(png: bytes) -> PILImage.Image:
    from PIL import Image

    with Image.open(io.BytesIO(png), formats=["PNG"]) as im:
        im.load()
        return im.convert("RGBA")


def _px(im: PILImage.Image, xy: tuple[int, int]) -> tuple[int, ...]:
    """One RGBA pixel (`im` is RGBA)."""
    return cast(tuple[int, ...], im.getpixel(xy))


def _content_box(im: PILImage.Image) -> tuple[tuple[int, int, int, int], tuple[int, ...]]:
    """The mark's box inside the logo's square, and the colour around it: clear when the logo
    has clear margins, else the colour of its corners (a mark on white or on a coloured tile)."""
    from PIL import ImageChops

    alpha = im.getchannel("A")
    w, h = im.width - 1, im.height - 1
    corners = [_px(im, p) for p in ((0, 0), (w, 0), (0, h), (w, h))]
    if min(c[3] for c in corners) < 16:  # clear margins: the mark is what's not clear
        box = alpha.point(lambda a: 255 if a > 16 else 0).getbbox()
        return box or (0, 0, im.width, im.height), (0, 0, 0, 0)
    bg = tuple(sorted(c[i] for c in corners)[1] for i in range(4))  # robust to one odd corner
    from PIL import Image

    flat = Image.new("RGBA", im.size, bg)
    diff = ImageChops.difference(im, flat).convert("L").point(lambda v: 255 if v > 24 else 0)
    return diff.getbbox() or (0, 0, im.width, im.height), bg


def _square(im: PILImage.Image) -> PILImage.Image:
    """The mark cut out with a little room around it, on a square of the colour around it."""
    from PIL import Image

    box, bg = _content_box(im)
    mark = im.crop(box)
    side = max(mark.size)
    side = int(side * 1.08) + 1  # a 4 % margin each side, so the mark doesn't touch the edge
    sq = Image.new("RGBA", (side, side), bg)
    sq.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2))
    return sq


def logo_png(png: bytes, size: int) -> bytes:
    """The logo made into a tab icon (16, 32) or a home-screen icon (180, on white unless the
    logo brings its own background: a phone fills clear corners with black)."""
    from PIL import Image

    if size not in SIZES:
        raise ValueError(size)
    sq = _square(_open(png))
    if size >= 180:
        if _px(sq, (0, 0))[3] < 16:  # a clear mark: on white, with room around it
            canvas = Image.new("RGBA", (180, 180), (255, 255, 255, 255))
            canvas.alpha_composite(sq.resize((152, 152), Image.Resampling.LANCZOS), (14, 14))
            return _png(canvas.convert("RGB"))
        return _png(sq.resize((180, 180), Image.Resampling.LANCZOS).convert("RGB"))
    return _png(sq.resize((size, size), Image.Resampling.LANCZOS))


# ---------------------------------------------------------------------------------------------
# Does the logo read at tab size? Simple rules, no model


WIDE = 2.4  # the mark's long side over its short side: a wordmark reads as a thin line
FAINT = 1.6  # contrast between the darkest and lightest parts on a light tab
BUSY = 0.36  # share of a 32 px icon's pixels that are edges: a crest or a photo turns to mush


def check(png: bytes) -> str | None:
    """None when the logo reads at tab size, else why not: wide · faint · busy (REASONS)."""
    import numpy as np
    from PIL import Image, ImageFilter

    im = _open(png)
    (x0, y0, x1, y1), _ = _content_box(im)
    w, h = max(x1 - x0, 1), max(y1 - y0, 1)
    if max(w / h, h / w) > WIDE:
        return "wide"
    small = Image.open(io.BytesIO(logo_png(png, 32))).convert("RGBA")
    tab = Image.new("RGBA", small.size, (255, 255, 255, 255))  # a light tab strip
    tab.alpha_composite(small)
    lum = _luminance(np.asarray(tab.convert("RGB"), dtype=np.float64) / 255.0)
    lo, hi = np.percentile(lum, 2), np.percentile(lum, 98)
    if (hi + 0.05) / (lo + 0.05) < FAINT:
        return "faint"
    edges = np.asarray(tab.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float64)
    inner = edges[1:-1, 1:-1]  # FIND_EDGES marks the frame itself
    if float((inner > 48).mean()) > BUSY:
        return "busy"
    return None


def _luminance(rgb: Any) -> Any:
    lin = (rgb <= 0.03928) * (rgb / 12.92) + (rgb > 0.03928) * (((rgb + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


# ---------------------------------------------------------------------------------------------
# Stored next to the logo: companies/<id>/logo/<sha>-<size>.png


def _logo_folder(settings: Settings, company_id: uuid.UUID) -> Path:
    from othor.core import logo

    return logo.folder(settings, company_id)


def store_logo_icons(settings: Settings, company_id: uuid.UUID, sha: str, png: bytes) -> None:
    """Made once, when the logo is uploaded."""
    d = _logo_folder(settings, company_id)
    d.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        (d / f"{sha}-{size}.png").write_bytes(logo_png(png, size))


def read(settings: Settings, company: Any, version: str, size: int) -> bytes | None:
    """The bytes for /tab-icon/<version>-<size>.png, or None (a 404) when the version is not
    the company's current icon."""
    from othor.core import logo

    if size not in SIZES or not VERSION.match(version):
        return None
    icon = choose(company)
    if icon is None or icon.version != version:
        return None
    if icon.kind == "letters":
        return letters_png(icon.text, size)
    p = _logo_folder(settings, company.id) / f"{icon.logo_sha}-{size}.png"
    if p.is_file():
        return p.read_bytes()
    src = logo.read(settings, company.id, icon.logo_sha)  # a logo from before 0.37: made now
    if src is None:
        return None
    store_logo_icons(settings, company.id, icon.logo_sha or "", src)
    return p.read_bytes()
