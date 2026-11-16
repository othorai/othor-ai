# SPDX-License-Identifier: Apache-2.0
"""Upload hardening (M11b §6b; docs/security.md §4b): the server refuses unknown types and files
whose content isn't what their name says, caps zip expansion before opening, and reads each file
in the worker under a time limit and a memory ceiling."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

from othor.setup import filecheck, limits, parse
from othor.setup.uploads import UploadRefused

MISMATCH = "This file isn’t the kind its name says."


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_the_sample_files_pass(upload_files: dict[str, Path]) -> None:
    for name, path in upload_files.items():
        filecheck.check(name, path.read_bytes())


def test_a_renamed_program_is_refused() -> None:
    exe = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200
    for name in ("report.xlsx", "board.pdf", "notes.docx", "photo.png", "photo.jpg"):
        with pytest.raises(UploadRefused, match=MISMATCH):
            filecheck.check(name, exe)


def test_unknown_types_are_refused_on_the_server() -> None:
    for name in ("tool.exe", "archive.zip", "page.html", "noextension"):
        with pytest.raises(UploadRefused, match="Othor AI reads"):
            filecheck.check(name, b"hello")


def test_office_files_need_their_parts() -> None:
    with pytest.raises(UploadRefused, match=MISMATCH):
        filecheck.check("book.xlsx", _zip({"word/document.xml": b"<w/>"}))
    with pytest.raises(UploadRefused, match=MISMATCH):
        filecheck.check("doc.docx", _zip({"xl/workbook.xml": b"<w/>"}))
    filecheck.check(
        "doc.docx", _zip({"[Content_Types].xml": b"<t/>", "word/document.xml": b"<d/>"})
    )


def test_text_kinds_must_be_utf8() -> None:
    filecheck.check("a.csv", "﻿name,₹\nx,1\n".encode())
    filecheck.check("q.sql", b"SELECT 1;")
    for name in ("a.csv", "q.sql", "n.txt", "n.md"):
        with pytest.raises(UploadRefused, match="UTF-8"):
            filecheck.check(name, b"name,\xe9t\xe9\n")
        with pytest.raises(UploadRefused, match="UTF-8"):
            filecheck.check(name, b"text\x00with a nul")


def test_images_carry_their_signature() -> None:
    filecheck.check("a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    filecheck.check("a.jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    filecheck.check("a.gif", b"GIF89a" + b"\x00" * 20)
    filecheck.check("a.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 20)
    with pytest.raises(UploadRefused, match=MISMATCH):
        filecheck.check("a.png", b"\xff\xd8\xff\xe0" + b"\x00" * 20)  # a JPEG named .png


def test_a_zip_bomb_is_refused_before_it_expands() -> None:
    buf = io.BytesIO()
    zeros = b"\x00" * (1024 * 1024)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", b"<t/>")
        z.writestr("xl/workbook.xml", b"<w/>")
        with z.open("xl/worksheets/sheet1.xml", "w", force_zip64=True) as f:
            for _ in range(201):
                f.write(zeros)
    bomb = buf.getvalue()
    assert len(bomb) < 2 * 1024 * 1024  # small on the wire
    with pytest.raises(UploadRefused, match="too large once opened"):
        filecheck.check("bomb.xlsx", bomb)


def test_too_many_entries_is_refused() -> None:
    entries = {"[Content_Types].xml": b"<t/>", "xl/workbook.xml": b"<w/>"}
    entries.update({f"xl/m/{i}.xml": b"" for i in range(10_000)})
    with pytest.raises(UploadRefused, match="too large once opened"):
        filecheck.check("many.xlsx", _zip(entries))


def test_xml_inside_xlsx_is_read_through_defusedxml() -> None:
    import openpyxl.xml

    assert openpyxl.xml.DEFUSEDXML is True


# --- the per-file limits in the worker ----------------------------------------------------------


def test_a_reader_that_hangs_times_out_as_unreadable() -> None:
    got = limits.read_limited(
        b"%PDF-1.7", "pdf", "slow.pdf", timeout_s=1.0, reader="tests.unit.setup.slow_readers:hang"
    )
    assert got.status == "unreadable" and got.summary["text"] == "Couldn’t be read"


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is enforced on Linux only")
def test_a_reader_over_the_memory_ceiling_is_unreadable() -> None:
    got = limits.read_limited(
        b"x", "text", "big.txt", timeout_s=30.0, reader="tests.unit.setup.slow_readers:hog"
    )
    assert got.status == "unreadable"


def test_a_reader_that_dies_is_unreadable() -> None:
    got = limits.read_limited(
        b"x", "text", "x.txt", timeout_s=30.0, reader="tests.unit.setup.slow_readers:crash"
    )
    assert got.status == "unreadable"


def test_the_real_reader_gives_the_same_result_in_the_child(upload_files) -> None:
    from tests.fixtures.sample_company.uploads import MIS, RECV_SQL

    for name in (MIS, RECV_SQL):
        raw = upload_files[name].read_bytes()
        kind = parse.kind_of(name)
        assert limits.read_limited(raw, kind, name) == parse.parse_file(raw, kind, name)
