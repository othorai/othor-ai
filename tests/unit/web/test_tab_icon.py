# SPDX-License-Identifier: Apache-2.0
"""Company identity 1.0 · the tab icon: the letters from the name, the logo made into 16, 32 and
180 px icons, the version in every URL, and the rules that say a logo won't read at tab size
(no model)."""

from __future__ import annotations

import io
import random
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from othor.core import logo
from othor.core.config import Settings
from othor.web import tab_icon

FONT = str(tab_icon.FONTS / "Inter-700.ttf")


def _clean(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return logo.clean(buf.getvalue())


def _circle(fill=(30, 60, 200, 255)) -> bytes:
    im = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((50, 50, 350, 350), fill=fill)
    return _clean(im)


def _wordmark() -> bytes:
    im = Image.new("RGB", (800, 200), (255, 255, 255))
    ImageDraw.Draw(im).text(
        (20, 40), "INDIGO THREAD", font=ImageFont.truetype(FONT, 110), fill=(20, 20, 80)
    )
    return _clean(im)


def _busy() -> bytes:
    rng = random.Random(1)
    im = Image.new("RGB", (400, 400), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for _ in range(1500):
        x, y = rng.randrange(400), rng.randrange(400)
        end = (x + rng.randrange(-30, 30), y + rng.randrange(-30, 30))
        d.line((x, y, *end), fill=(rng.randrange(255), 0, rng.randrange(255)), width=2)
    return _clean(im)


def _company(name: str = "Indigo Thread Co.", sha: str | None = None, tab: str | None = None):
    return SimpleNamespace(id=uuid.uuid4(), name=name, logo_sha=sha, tab_icon=tab)


@pytest.mark.parametrize(
    "name, want",
    [
        ("Indigo Thread Co.", "IT"),
        ("ABG Demo", "AD"),
        ("acme", "AC"),
        ("3M India", "3I"),
        ("  Café  Noir ", "CN"),
        ("O'Brien & Sons", "OB"),
        ("X", "X"),
        ("सुद लाइफ", ""),  # no font for it: the Othor AI icon
        ("", ""),
    ],
)
def test_the_letters_are_the_first_two_of_the_name(name: str, want: str) -> None:
    assert tab_icon.letters(name) == want


def test_the_letters_icon_is_drawn_at_each_size_the_same_every_time() -> None:
    for size in tab_icon.SIZES:
        png = tab_icon.letters_png("IT", size)
        im = Image.open(io.BytesIO(png))
        assert im.size == (size, size)
        assert png == tab_icon.letters_png.__wrapped__("IT", size)  # same inputs, same bytes
    # the tab icons have clear rounded corners; the home-screen icon is a full square
    assert (
        Image.open(io.BytesIO(tab_icon.letters_png("IT", 32))).convert("RGBA").getpixel((0, 0))[3]
        == 0
    )
    assert Image.open(io.BytesIO(tab_icon.letters_png("IT", 180))).mode == "RGB"
    with pytest.raises(ValueError):
        tab_icon.letters_png("IT", 64)


def test_the_letters_use_the_brand_colours() -> None:
    im = Image.open(io.BytesIO(tab_icon.letters_png("IT", 180))).convert("RGB")
    assert im.getpixel((2, 2)) == (0x0F, 0x17, 0x2A)  # slate
    colours = {c for _, c in im.getcolors(180 * 180)}
    assert (0x2C, 0xCA, 0xFF) in colours  # accent cyan, inside the letters


def test_which_icon_and_its_version() -> None:
    plain = tab_icon.choose(_company())
    assert plain is not None and plain.kind == "letters" and plain.text == "IT"
    assert plain.url(32) == f"/tab-icon/{plain.version}-32.png"
    # a rename changes the letters' version; the same name gives the same one
    renamed = tab_icon.choose(_company("ABG Demo"))
    assert renamed is not None and renamed.version != plain.version
    assert tab_icon.choose(_company()).version == plain.version  # type: ignore[union-attr]
    # a logo wins, by its hash; unless the owner chose the letters
    with_logo = tab_icon.choose(_company(sha="a" * 64))
    assert with_logo is not None and with_logo.kind == "logo"
    other = tab_icon.choose(_company(sha="b" * 64))
    assert other is not None and other.version != with_logo.version
    chose = tab_icon.choose(_company(sha="a" * 64, tab="letters"))
    assert chose is not None and chose.kind == "letters"
    # nothing to draw: the Othor AI icon
    assert tab_icon.choose(_company("सुद लाइफ")) is None
    assert tab_icon.links(_company("सुद लाइफ")) is None
    assert set(tab_icon.links(_company()) or {}) == {"16", "32", "180"}


def test_the_logo_is_made_into_icons_with_its_margins_trimmed() -> None:
    src = _circle()
    for size in tab_icon.SIZES:
        im = Image.open(io.BytesIO(tab_icon.logo_png(src, size))).convert("RGBA")
        assert im.size == (size, size)
    # the circle fills the 32 px icon (its clear margins cut), not a speck in the middle
    im = Image.open(io.BytesIO(tab_icon.logo_png(src, 32))).convert("RGBA")
    assert im.getpixel((16, 2))[3] > 200 and im.getpixel((0, 0))[3] == 0
    # the home-screen icon has no clear corners (a phone fills them black): white around the mark
    home = Image.open(io.BytesIO(tab_icon.logo_png(src, 180)))
    assert home.mode == "RGB" and home.getpixel((2, 2)) == (255, 255, 255)


def test_a_logo_that_reads_passes_and_one_that_wont_says_why() -> None:
    assert tab_icon.check(_circle()) is None
    assert tab_icon.check(_wordmark()) == "wide"
    assert tab_icon.check(_circle((250, 250, 250, 255))) == "faint"  # white on clear
    assert tab_icon.check(_busy()) == "busy"
    assert set(tab_icon.REASONS) == {"wide", "faint", "busy"}


def test_the_icons_are_stored_with_the_logo_and_served_by_version(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    c = _company()
    png = _circle()
    sha = logo.store(settings, c.id, png)
    c.logo_sha = sha
    tab_icon.store_logo_icons(settings, c.id, sha, png)
    for size in tab_icon.SIZES:
        assert (logo.folder(settings, c.id) / f"{sha}-{size}.png").is_file()
    v = tab_icon.choose(c).version  # type: ignore[union-attr]
    assert tab_icon.read(settings, c, v, 32) == tab_icon.logo_png(png, 32)
    # a logo uploaded before the icons existed: made on the first ask
    (logo.folder(settings, c.id) / f"{sha}-180.png").unlink()
    assert tab_icon.read(settings, c, v, 180) == tab_icon.logo_png(png, 180)
    # an old version, an odd size or a bad version: nothing
    assert tab_icon.read(settings, c, "0" * 16, 32) is None
    assert tab_icon.read(settings, c, v, 64) is None
    assert tab_icon.read(settings, c, "../../etc", 32) is None
    # the letters, chosen
    c.tab_icon = "letters"
    lv = tab_icon.choose(c).version  # type: ignore[union-attr]
    assert tab_icon.read(settings, c, lv, 16) == tab_icon.letters_png("IT", 16)
    assert tab_icon.read(settings, c, v, 16) is None
