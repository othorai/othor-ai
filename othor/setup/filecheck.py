# SPDX-License-Identifier: Apache-2.0
"""What an upload is, checked on the server before it's stored (M11b §6b; docs/security.md §4b).

The browser's file picker is a convenience, not a guard. Here the name must be a kind Othor AI
reads, the content must be that kind (zip parts for XLSX and DOCX, `%PDF`, image signatures,
UTF-8 for text), and a zip's central directory is read, never expanded, to cap what opening it
would take. Nothing here parses the file's contents.
"""

from __future__ import annotations

import io
import zipfile

from othor.setup.parse import KINDS, kind_of

MAX_UNZIPPED = 200 * 1024 * 1024
MAX_ENTRIES = 10_000

UNKNOWN = "Othor AI reads XLSX, CSV, SQL, PDF, Word, image and text files."
MISMATCH = "This file isn’t the kind its name says."
TOO_LARGE = "This file is too large once opened."
NOT_UTF8 = "Save this file as UTF-8 text and upload it again."

PARTS = {
    "xlsx": ("[Content_Types].xml", "xl/workbook.xml"),
    "docx": ("[Content_Types].xml", "word/document.xml"),
}
IMAGES = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
}


def _refuse(message: str) -> None:
    from othor.setup.uploads import UploadRefused

    raise UploadRefused(message)


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _zip(data: bytes, kind: str) -> None:
    if not zipfile.is_zipfile(io.BytesIO(data)):
        _refuse(MISMATCH)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()  # the central directory only; nothing is decompressed
    except (zipfile.BadZipFile, ValueError, OSError):
        _refuse(MISMATCH)
        return
    if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_UNZIPPED:
        _refuse(TOO_LARGE)
    names = {i.filename for i in infos}
    if not all(p in names for p in PARTS[kind]):
        _refuse(MISMATCH)


def _image(data: bytes, ext: str) -> None:
    if ext == "webp":
        ok = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    else:
        ok = data.startswith(IMAGES[ext])
    if not ok:
        _refuse(MISMATCH)


def _text(data: bytes) -> None:
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        _refuse(NOT_UTF8)
    if b"\x00" in data:
        _refuse(NOT_UTF8)


def check(name: str, data: bytes) -> None:
    """Raise UploadRefused unless `data` is the kind `name` says, within the zip caps."""
    ext = _ext(name)
    if ext not in KINDS:
        _refuse(UNKNOWN)
    kind = kind_of(name)
    if kind in PARTS:
        _zip(data, kind)
    elif kind == "pdf":
        if b"%PDF-" not in data[:1024]:
            _refuse(MISMATCH)
    elif kind == "image":
        _image(data, ext)
    else:  # csv, sql, text
        _text(data)
