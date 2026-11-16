# SPDX-License-Identifier: Apache-2.0
"""The company's logo (M15.7): a square mark (owner, 13 Nov 2026), PNG or JPEG up to 1 MB, judged
by its bytes, decoded and saved again as a PNG we wrote: metadata dropped, a wider or taller
image centred on a clear square, at most 512 × 512. What is served and
emailed is never the uploaded file. SVG and every other kind are refused (docs/security.md)."""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from pathlib import Path

from othor.core.config import Settings

MAX_BYTES = 1024 * 1024
MAX_SIDE = 512
MAX_PIXELS = 25_000_000  # a decompression bomb is refused before it is decoded
SHA = re.compile(r"^[0-9a-f]{64}$")


class LogoRefused(ValueError):
    pass


def _kind(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    return None


def clean(data: bytes) -> bytes:
    """The uploaded bytes → our PNG, or LogoRefused with the line the owner sees."""
    from PIL import Image

    if not data:
        raise LogoRefused("The file is empty.")
    if len(data) > MAX_BYTES:
        raise LogoRefused("Logos up to 1 MB.")
    kind = _kind(data)
    if kind is None:
        raise LogoRefused("A PNG or JPEG image, please.")
    try:
        with Image.open(io.BytesIO(data), formats=[kind]) as im:
            w, h = im.size
            if w * h > MAX_PIXELS or w < 1 or h < 1:
                raise LogoRefused("That image is too large to use as a logo.")
            im.load()
            out = im.convert("RGBA")
    except LogoRefused:
        raise
    except Exception as e:  # truncated, a bomb Pillow caught, or not what its bytes say
        raise LogoRefused("That image can't be read. Try another PNG or JPEG.") from e
    side = max(out.size)
    if out.size != (side, side):  # centred on a clear square, never cropped
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(out, ((side - out.width) // 2, (side - out.height) // 2))
        out = sq
    out.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)  # a fresh file: no EXIF, no text chunks
    return buf.getvalue()


def folder(settings: Settings, company_id: uuid.UUID) -> Path:
    return settings.company_dir(company_id) / "logo"


def store(settings: Settings, company_id: uuid.UUID, png: bytes) -> str:
    sha = hashlib.sha256(png).hexdigest()
    d = folder(settings, company_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sha}.png").write_bytes(png)
    return sha


def read(settings: Settings, company_id: uuid.UUID, sha: str | None) -> bytes | None:
    if not sha or not SHA.match(sha):
        return None
    p = folder(settings, company_id) / f"{sha}.png"
    return p.read_bytes() if p.is_file() else None


def url(sha: str | None) -> str | None:
    return f"/logo/{sha}.png" if sha else None
