# SPDX-License-Identifier: Apache-2.0
"""M15.7 · the logo: PNG or JPEG by its bytes, up to 1 MB, re-encoded as our own PNG."""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from othor.core import logo
from othor.core.config import Settings


def _img(fmt: str, size=(300, 120), exif: bool = False) -> bytes:
    im = Image.new("RGB", size, (20, 90, 160))
    buf = io.BytesIO()
    kw = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SecretCam"  # Make
        kw["exif"] = ex.tobytes()
    im.save(buf, format=fmt, **kw)
    return buf.getvalue()


def test_png_and_jpeg_become_our_png() -> None:
    for f in ("PNG", "JPEG"):
        out = logo.clean(_img(f))
        assert out.startswith(b"\x89PNG")
        with Image.open(io.BytesIO(out)) as im:
            assert im.size == (300, 300) and im.mode == "RGBA"  # square, the mark centred
            assert im.getpixel((0, 0))[3] == 0 and im.getpixel((150, 150))[3] == 255


def test_metadata_is_dropped_and_big_logos_shrink() -> None:
    out = logo.clean(_img("JPEG", (2000, 800), exif=True))
    assert b"SecretCam" not in out
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (512, 512) and not im.getexif()


@pytest.mark.parametrize(
    ("data", "line"),
    [
        (b"", "The file is empty."),
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>', "A PNG or JPEG"),
        (b"GIF89a" + b"\x00" * 20, "A PNG or JPEG"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, "can't be read"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (1024 * 1024 + 1), "up to 1 MB"),
    ],
)
def test_refused(data: bytes, line: str) -> None:
    with pytest.raises(logo.LogoRefused, match=line):
        logo.clean(data)


def test_a_decompression_bomb_is_refused() -> None:
    im = Image.new("1", (6000, 6000))  # 36 MP, tiny on disk
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    assert len(buf.getvalue()) < logo.MAX_BYTES
    with pytest.raises(logo.LogoRefused, match="too large"):
        logo.clean(buf.getvalue())


def test_store_and_read_by_hash(tmp_path) -> None:
    st = Settings(data_dir=tmp_path)
    cid = uuid.uuid4()
    png = logo.clean(_img("PNG"))
    sha = logo.store(st, cid, png)
    assert logo.read(st, cid, sha) == png
    assert logo.read(st, uuid.uuid4(), sha) is None  # another company's folder
    assert logo.read(st, cid, "../../etc/passwd") is None
    assert logo.url(sha) == f"/logo/{sha}.png" and logo.url(None) is None
