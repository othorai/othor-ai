# SPDX-License-Identifier: Apache-2.0
"""Company identity 1.0 · the tab icon on the company's own host: the letters with no logo, the
logo once uploaded, the letters suggested when the logo won't read at tab size and applied by the
owner, a rename that changes the letters, and othor.ai and sign-in keeping the Othor AI icon."""

from __future__ import annotations

import io
import re

import sqlalchemy as sa
from PIL import Image, ImageDraw, ImageFont

from othor.core.models import Company, Event
from othor.core.tenancy import system_scope, tenant_scope
from othor.web import tab_icon
from tests.web.conftest import ROOT, Env, signup

HX = {"HX-Request": "true"}
ICON = re.compile(r'<link id="tab-icon-32" rel="icon" href="(/tab-icon/([0-9a-f]{16})-32\.png)"')


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _mark() -> bytes:
    im = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((40, 40, 260, 260), fill=(200, 40, 40, 255))
    return _png(im)


def _wordmark() -> bytes:
    im = Image.new("RGB", (800, 200), (255, 255, 255))
    font = ImageFont.truetype(str(tab_icon.FONTS / "Inter-700.ttf"), 110)
    ImageDraw.Draw(im).text((20, 40), "INDIGO THREAD", font=font, fill=(20, 20, 80))
    return _png(im)


def _upload(env: Env, h: str, data: bytes):
    tok = env.csrf(h + "/settings")
    return env.client.post(
        h + "/settings/logo",
        files={"file": ("logo.png", data, "image/png")},
        headers={"X-CSRF-Token": tok, **HX},
    )


def _post(env: Env, h: str, path: str, data: dict[str, str]):
    tok = env.csrf(h + "/settings")
    return env.client.post(h + path, data=data, headers={"X-CSRF-Token": tok, **HX})


def _icon(html: str) -> tuple[str, str]:
    m = ICON.search(html)
    assert m, "no company tab icon on the page"
    return m.group(1), m.group(2)


def test_no_logo_the_letters_and_othor_ai_keeps_its_own(env: Env) -> None:
    signup(env)
    h = env.host("indigothread")
    home = env.client.get(h + "/home").text
    url, version = _icon(home)
    assert f'href="/tab-icon/{version}-16.png"' in home
    assert f'rel="apple-touch-icon" href="/tab-icon/{version}-180.png"' in home
    assert "/static/favicon.svg" not in home
    r = env.client.get(h + url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content == tab_icon.letters_png("IT", 32)
    assert "immutable" in r.headers["cache-control"] and r.headers["etag"] == f'"{version}-32"'
    again = env.client.get(h + url, headers={"If-None-Match": f'"{version}-32"'})
    assert again.status_code == 304 and not again.content
    assert Image.open(
        io.BytesIO(env.client.get(h + f"/tab-icon/{version}-180.png").content)
    ).size == (180, 180)
    # an old version, an odd size: nothing
    assert env.client.get(h + f"/tab-icon/{'0' * 16}-32.png").status_code == 404
    assert env.client.get(h + f"/tab-icon/{version}-64.png").status_code == 404
    # Settings says what the tab shows
    page = env.client.get(h + "/settings").text
    assert "The browser tab shows the letters IT" in page
    # othor.ai and the company's sign-in page keep the Othor AI icon
    for html in (env.client.get(ROOT + "/").text, env.client.get(ROOT + "/signup").text):
        assert "/static/favicon.svg" in html and "/tab-icon/" not in html
    assert env.client.get(ROOT + f"/tab-icon/{version}-32.png").status_code == 404
    env.client.cookies.clear()
    login = env.client.get(h + "/login").text
    assert "/static/favicon.svg" in login and "/tab-icon/" not in login


def test_a_rename_changes_the_letters_at_once(env: Env) -> None:
    signup(env)
    h = env.host("indigothread")
    _, before = _icon(env.client.get(h + "/home").text)
    r = _post(env, h, "/settings/name", {"company_name": "ABG Demo"})
    assert r.status_code == 200
    # the head's links swapped in place, no reload
    m = re.search(
        r'<link id="tab-icon-32"[^>]*href="/tab-icon/([0-9a-f]{16})-32\.png"[^>]*hx-swap-oob',
        r.text,
    )
    assert m and m.group(1) != before
    _, after = _icon(env.client.get(h + "/home").text)
    assert after == m.group(1)
    assert env.client.get(h + f"/tab-icon/{after}-32.png").content == tab_icon.letters_png("AD", 32)
    assert env.client.get(h + f"/tab-icon/{before}-32.png").status_code == 404


def test_a_logo_that_reads_becomes_the_tab_icon(env: Env) -> None:
    signup(env)
    h = env.host("indigothread")
    r = _upload(env, h, _mark())
    assert r.status_code == 200 and "The browser tab shows your logo" in r.text
    assert 'hx-swap-oob="outerHTML"' in r.text and "/tab-icon/" in r.text
    url, version = _icon(env.client.get(h + "/home").text)
    data = env.client.get(h + url).content
    assert data.startswith(b"\x89PNG") and data != tab_icon.letters_png("IT", 32)
    for size in tab_icon.SIZES:
        got = env.client.get(h + f"/tab-icon/{version}-{size}.png")
        assert got.status_code == 200
        assert Image.open(io.BytesIO(got.content)).size == (size, size)
    # removed: the letters again, with a new version
    tok = env.csrf(h + "/settings")
    env.client.post(h + "/settings/logo/remove", headers={"X-CSRF-Token": tok, **HX})
    _, letters_v = _icon(env.client.get(h + "/home").text)
    assert letters_v != version
    assert env.client.get(h + url).status_code == 404


def test_a_wide_logo_gets_the_letters_suggested_and_the_owner_applies_them(env: Env) -> None:
    signup(env)
    h = env.host("indigothread")
    r = _upload(env, h, _wordmark())
    assert (
        "Your logo is too wide to read in a browser tab" in r.text and "Use the letters" in r.text
    )
    _, logo_v = _icon(env.client.get(h + "/home").text)  # a suggestion: the logo until applied
    r = _post(env, h, "/settings/tab-icon", {"choice": "letters"})
    assert r.status_code == 200 and "The browser tab shows the letters IT" in r.text
    assert "Use the logo" in r.text and 'hx-swap-oob="outerHTML"' in r.text
    url, letters_v = _icon(env.client.get(h + "/home").text)
    assert letters_v != logo_v
    assert env.client.get(h + url).content == tab_icon.letters_png("IT", 32)
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        cid = s.scalars(sa.select(Company.id)).one()
    with sf() as s, tenant_scope(cid):
        ev = s.scalars(sa.select(Event).where(Event.action == "company.tab_icon_set")).one()
        assert ev.before == {"tab_icon": "logo"} and ev.after == {"tab_icon": "letters"}
    # back to the logo
    r = _post(env, h, "/settings/tab-icon", {"choice": "logo"})
    assert "Your logo is too wide" in r.text
    assert _icon(env.client.get(h + "/home").text)[1] == logo_v
    # a new logo starts from it again
    _post(env, h, "/settings/tab-icon", {"choice": "letters"})
    _upload(env, h, _mark())
    assert "The browser tab shows your logo" in env.client.get(h + "/settings").text
    assert _post(env, h, "/settings/tab-icon", {"choice": "other"}).status_code == 400
