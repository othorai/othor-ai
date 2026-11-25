# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
"""The README's tour (owner, 21 and 25 Nov 2026): about 30 s of the real app on the USD sample,
recorded at 1920×1080: Home → the Morning Brief → a finding's "How it's calculated" → Sweep →
the Action plan → the Business Model. No Forecasts (Enterprise Edition; the public repo is the
open core). Dev only, run by hand against `make demo`'s app:

    make demo                       # prints the one-time sign-in link
    .venv/bin/python tests/ui/preview_readme_tour.py '<the link>'

The page is captured with Chromium's screencast (each frame as it is drawn, with its time), a
cursor drawn on the page, then encoded by ffmpeg to an animated WebP that plays by itself:
about 1600 px wide, quality 80, 12 fps, looping, under 8 MB (1440 px if it is over). Writes
export/docs/images/readme-tour.webp, and the WebP and a contact sheet of its frames to
~/Desktop/othor-preview/demo-tour/ for the owner's OK."""

from __future__ import annotations

import argparse
import base64
import itertools
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "export" / "docs" / "images" / "readme-tour.webp"
PREVIEW = Path.home() / "Desktop" / "othor-preview" / "demo-tour"
W, H = 1920, 1080
MAX_BYTES = 8_000_000
FPS = 12
AT = {"xy": (W * 0.6, H * 0.55)}  # where the cursor is

# a cursor the recording can see (headless Chromium draws none): a white-edged arrow that follows
# the mouse and dips on a click
CURSOR = """
(() => {
  const add = () => {
    if (document.getElementById('tour-cursor')) return;
    const c = document.createElement('div');
    c.id = 'tour-cursor';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 26 26"><path d="M3 2 L3 21 L8.5 16 L12.5 24.5 L16 23 L12 14.8 L19.5 14.8 Z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, {position: 'fixed', left: '0', top: '0', zIndex: 2147483647,
      pointerEvents: 'none', transform: 'translate(' + (window.__tx || -40) + 'px,' + (window.__ty || -40) + 'px)',
      transition: 'scale .12s', filter: 'drop-shadow(0 1px 2px rgba(0,0,0,.35))'});
    document.documentElement.appendChild(c);
  };
  document.addEventListener('mousemove', e => {
    window.__tx = e.clientX; window.__ty = e.clientY; add();
    document.getElementById('tour-cursor').style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)';
  }, true);
  document.addEventListener('mousedown', () => { const c = document.getElementById('tour-cursor'); if (c) c.style.scale = '.82'; }, true);
  document.addEventListener('mouseup', () => { const c = document.getElementById('tour-cursor'); if (c) c.style.scale = '1'; }, true);
  if (document.readyState !== 'loading') add(); else document.addEventListener('DOMContentLoaded', add);
})();
"""


class Tape:
    """Chromium's screencast: every drawn frame with its timestamp, kept in memory."""

    def __init__(self, page: Any) -> None:
        self.cdp = page.context.new_cdp_session(page)
        self.frames: list[tuple[float, bytes]] = []
        self.on = False
        self.cdp.on("Page.screencastFrame", self._frame)

    def _frame(self, f: dict[str, Any]) -> None:
        self.cdp.send("Page.screencastFrameAck", {"sessionId": f["sessionId"]})
        if self.on:
            self.frames.append((time.monotonic(), base64.b64decode(f["data"])))

    def start(self) -> None:
        self.on = True
        self.cdp.send(
            "Page.startScreencast",
            {"format": "png", "maxWidth": W, "maxHeight": H, "everyNthFrame": 1},
        )

    def stop(self) -> float:
        self.on = False
        self.cdp.send("Page.stopScreencast")
        return time.monotonic()


def _glide(pg: Any, x: float, y: float, ms: int = 600) -> None:
    """Move the cursor to (x, y) over `ms`, eased."""
    x0, y0 = AT["xy"]
    n = max(6, ms // 33)
    for i in range(1, n + 1):
        u = i / n
        e = u * u * (3 - 2 * u)
        pg.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
        pg.wait_for_timeout(ms / n)
    AT["xy"] = (x, y)


def _click(pg: Any, selector: str, ms: int = 650, *, moves: bool = True) -> None:
    """Glide to the element and click it; `moves`: wait until the address changes (a page, or an
    in-place panel that pushes its address)."""
    before = pg.url
    el = pg.locator(selector).first
    el.scroll_into_view_if_needed()
    box = el.bounding_box()
    assert box, selector
    _glide(pg, box["x"] + min(box["width"] / 2, 60), box["y"] + box["height"] / 2, ms)
    pg.wait_for_timeout(150)
    pg.mouse.down()
    pg.wait_for_timeout(90)
    pg.mouse.up()
    if moves:
        pg.wait_for_url(lambda u: u != before)


def _scroll(pg: Any, to: float, ms: int = 500) -> None:
    y0 = pg.evaluate("scrollY")
    n = max(6, ms // 40)
    for i in range(1, n + 1):
        u = i / n
        pg.evaluate(f"window.scrollTo(0, {y0 + (to - y0) * (u * u * (3 - 2 * u)):.0f})")
        pg.wait_for_timeout(ms / n)


def _settle(pg: Any) -> None:
    from playwright.sync_api import Error

    for _ in range(20):
        try:
            pg.wait_for_load_state("networkidle")
            pg.evaluate("document.fonts.ready")
            break
        except Error:  # still navigating
            pg.wait_for_timeout(100)
    pg.wait_for_timeout(250)


def record(link: str, tmp: Path) -> tuple[list[tuple[float, bytes]], float]:
    from playwright.sync_api import sync_playwright

    parts = urlsplit(link)
    app = f"{parts.scheme}://{parts.netloc}"
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        ctx = b.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
        ctx.add_init_script(CURSOR)
        pg = ctx.new_page()
        pg.goto(link, wait_until="networkidle")  # the one-time link: signs in, lands on Home
        assert "/login" not in pg.url, f"the link didn't sign in (used or expired?): {pg.url}"
        # warm every page once (fonts, CSS, charts) so the recording starts on drawn pages
        brief = pg.locator("a[href^='/brief/']").first.get_attribute("href")
        assert brief, "no brief link on Home"
        for path in ("/", brief, "/sweep", "/action-plan", "/ontology"):
            pg.goto(app + path, wait_until="networkidle")
        pg.goto(app + "/", wait_until="networkidle")
        _settle(pg)
        tape = Tape(pg)
        pg.mouse.move(*AT["xy"])
        tape.start()
        pg.wait_for_timeout(1900)  # Home: this morning's lines (about 2 s)

        # the Morning Brief, scrolled in steps: hold, then a short glide (about 7 s)
        _click(pg, f"a[href='{brief}']")
        _settle(pg)
        pg.wait_for_timeout(1100)
        height = pg.evaluate("document.documentElement.scrollHeight - innerHeight")
        for k in (1, 2, 3):
            _scroll(pg, height * k / 3 * 0.8, 450)
            pg.wait_for_timeout(850)
        _scroll(pg, 0, 500)
        pg.wait_for_timeout(250)

        # a finding beside the brief, then its "How it's calculated" tab (about 5 s)
        _click(pg, "a[href*='/finding/']")
        pg.wait_for_selector(".pane-tabs")
        _settle(pg)
        pg.wait_for_timeout(900)
        _click(pg, ".pane-tabs a[href*='tab=test']")
        _settle(pg)
        pg.wait_for_timeout(2300)
        # close the panel (its shade covers the menu), back on the brief
        _click(pg, "a.scrim", 400)
        _settle(pg)

        # Sweep, the record of the night (about 5 s)
        _click(pg, "a[href='/sweep']")
        _settle(pg)
        pg.wait_for_timeout(1700)
        _scroll(pg, 520, 550)
        pg.wait_for_timeout(1100)

        # the Action plan (about 4 s)
        _click(pg, "a[href='/action-plan']")
        _settle(pg)
        pg.wait_for_timeout(2400)

        # the Business Model (about 4 s)
        _click(pg, "a[href='/ontology']")
        _settle(pg)
        pg.wait_for_timeout(2700)
        end = tape.stop()
        frames = tape.frames
        b.close()
    return frames, end


def encode(frames: list[tuple[float, bytes]], end: float, tmp: Path, width: int) -> Path:
    """The frames sampled on a 12 fps clock (the frame showing at each tick, one file per tick,
    hard-linked when it repeats), then scaled, as a looping animated WebP; libwebp keeps a held
    page as one frame. (ffmpeg's concat demuxer stretched short entries, so the clock is here.)"""
    t0, step = frames[0][0], 1 / FPS
    seq = tmp / f"seq{width}"
    seq.mkdir()
    i = 0
    for k in range(int((end - t0) / step)):
        while i + 1 < len(frames) and frames[i + 1][0] - t0 <= k * step:
            i += 1
        src = tmp / f"f{i:05d}.png"
        if not src.exists():
            src.write_bytes(frames[i][1])
        (seq / f"t{k:05d}.png").hardlink_to(src)
    out = tmp / f"tour-{width}.webp"
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-framerate", str(FPS), "-i", str(seq / "t%05d.png"),
            "-vf", f"scale={width}:-2:flags=lanczos",
            "-c:v", "libwebp_anim", "-lossless", "0", "-quality", "80",
            "-compression_level", "6", "-loop", "0", "-an", str(out),
        ],
        check=True,
    )  # fmt: skip
    return out


def durations(webp: Path) -> list[int]:
    """Each frame's time in ms, from libwebp's webpmux (Pillow doesn't report them)."""
    out = subprocess.run(
        [shutil.which("webpmux") or "webpmux", "-info", str(webp)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [int(ln.split()[6]) for ln in out.splitlines() if ln.strip()[:1].isdigit()]


def contact_sheet(webp: Path, dest: Path, every: float = 1.5) -> None:
    """The frame showing at every `every` seconds, four to a row, 480 px each."""
    from PIL import Image  # ffmpeg can't read an animated WebP; Pillow can

    im = Image.open(webp)
    t, shots, k = 0.0, [], 0
    for i, ms in enumerate(durations(webp)):
        im.seek(i)
        while t + ms / 1000 > k * every:
            shots.append(im.convert("RGB").copy())
            k += 1
        t += ms / 1000
    cols, tw = 4, 480
    th = round(tw * shots[0].height / shots[0].width)
    rows = (len(shots) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * 8, rows * th + (rows + 1) * 8), "white")
    for n, s in enumerate(shots):
        r, c = divmod(n, cols)
        sheet.paste(
            s.resize((tw, th), Image.Resampling.LANCZOS), (8 + c * (tw + 8), 8 + r * (th + 8))
        )
    sheet.save(dest, quality=88)


def plays_by_itself(webp: Path) -> int:
    """Open the WebP in headless Chromium as the README's <img> and take a shot every 1.5 s
    with no click: returns how many of the shots differ from the one before (0 = it doesn't play)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1700, "height": 1000})
        page = Path(tempfile.mkdtemp(prefix="plays-")) / "readme.html"
        page.write_text(f'<img src="{webp.resolve().as_uri()}" width="1600">')
        pg.goto(page.as_uri())
        pg.wait_for_function("document.images[0].complete")
        shots = []
        for _ in range(6):
            shots.append(pg.screenshot())
            pg.wait_for_timeout(1500)
        b.close()
    return sum(1 for a, z in itertools.pairwise(shots) if a != z)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("link", help="the one-time sign-in link `make demo` printed")
    ap.add_argument("--width", type=int, default=1600)
    a = ap.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="readme-tour-"))
    frames, end = record(a.link, tmp)
    assert frames, "no frames recorded"
    secs = end - frames[0][0]
    out = encode(frames, end, tmp, a.width)
    if out.stat().st_size > MAX_BYTES:
        out = encode(frames, end, tmp, 1440)
    size = out.stat().st_size
    assert size <= MAX_BYTES, f"{size / 1e6:.1f} MB at 1440 px"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, OUT)
    PREVIEW.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, PREVIEW / OUT.name)
    contact_sheet(out, PREVIEW / "contact-sheet.jpg")
    from PIL import Image

    im = Image.open(out)
    changed = plays_by_itself(OUT)
    print(
        f"{OUT.relative_to(ROOT)}: {size / 1e6:.2f} MB, {im.width}×{im.height}, "
        f"{getattr(im, 'n_frames', 1)} frames, {secs:.1f} s recorded ({len(frames)} screencast frames); "
        f"in Chromium with no click, {changed} of 5 shots 1.5 s apart moved on; preview in {PREVIEW}"
    )
    return 0 if changed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
