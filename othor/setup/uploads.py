# SPDX-License-Identifier: Apache-2.0
"""Uploads: stored encrypted under the company's folder, read in the worker (docs/02 "Files").

Code reads what it can (XLSX, CSV, SQL, text PDFs). With the model, PDFs and images are also read
by Claude; its numbers are checked against the PDF's own text, and cut from images.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.setup import parse
from othor.setup.models import Upload, UploadRef

MAX_BYTES = 25 * 1024 * 1024


class UploadRefused(ValueError):
    pass


def _aad(company_id: uuid.UUID, sha: str) -> bytes:
    return f"upload:{company_id}:{sha}".encode()


def add(
    s: Session,
    settings: Settings,
    key: InstanceKey,
    company_id: uuid.UUID,
    *,
    filename: str,
    data: bytes,
    actor: str,
) -> tuple[Upload, bool]:
    """Store one file (encrypted). Returns (upload, created); a duplicate returns the first."""
    if not data:
        raise UploadRefused("The file is empty.")
    if len(data) > MAX_BYTES:
        raise UploadRefused("Files up to 25 MB.")
    from othor.setup import filecheck

    filecheck.check(Path(filename).name, data)  # the kind its name says, within the zip caps
    sha = hashlib.sha256(data).hexdigest()
    same = s.scalars(sa.select(Upload).where(Upload.sha256 == sha)).first()
    if same is not None:
        return same, False
    name = Path(filename).name[:300] or "file"
    folder = settings.uploads_dir(company_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha}.bin"
    path.write_bytes(key.encrypt(data, aad=_aad(company_id, sha)))
    up = Upload(
        filename=name,
        kind=parse.kind_of(name),
        content_type=parse.media_type(name),
        size=len(data),
        sha256=sha,
        storage_path=path.name,
        status="added",
        uploaded_by=actor,
    )
    s.add(up)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="upload.added",
        object_type="upload",
        object_id=up.id,
        after={"filename": name, "kind": up.kind, "size": up.size},
    )
    return up, True


def read_bytes(settings: Settings, key: InstanceKey, up: Upload) -> bytes:
    raw = (settings.uploads_dir(up.company_id) / up.storage_path).read_bytes()
    return key.decrypt(raw, aad=_aad(up.company_id, up.sha256))


def store_parse(s: Session, up: Upload, got: parse.Parsed) -> None:
    s.execute(sa.delete(UploadRef).where(UploadRef.upload_id == up.id))
    for r in got.refs:
        s.add(
            UploadRef(
                upload_id=up.id,
                locator=r.locator,
                kind=r.kind,
                text=r.text[:5000],
                data=r.data or None,
            )
        )
    before = up.status
    up.status, up.read_by, up.summary = got.status, got.read_by, got.summary
    record_event(
        s,
        actor=SYSTEM_ACTOR,
        action="upload.read",
        object_type="upload",
        object_id=up.id,
        before={"status": before},
        after={"status": got.status, "refs": len(got.refs)},
    )


def model_read(gateway: Any, company_id: uuid.UUID, up: Upload, raw: bytes) -> parse.Parsed | None:
    """Claude reads a PDF or an image. None when the model is unavailable."""
    from othor.ai.gateway import Attachment

    if up.kind not in ("pdf", "image") or gateway is None:
        return None
    att = Attachment("pdf" if up.kind == "pdf" else "image", up.content_type, raw, up.filename)
    res = gateway.complete(
        company_id,
        "read_upload",
        "v1",
        {"filename": up.filename, "kind": up.kind},
        attachments=(att,),
    )
    if not res.ok or not res.output:
        return None
    source_text = parse.pdf_text(raw) if up.kind == "pdf" else None
    refs = parse.refs_from_model(res.output, kind=up.kind, source_text=source_text)
    if up.kind == "pdf":  # keep the page text too: rule drafts and search use it
        refs += parse.parse_pdf_as_text(raw).refs
    counts: dict[str, int] = {}
    for r in refs:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    words = {"target": "target", "metric": "metric", "dimension": "dimension", "term": "term"}
    parts = [
        f"{n} {words[k]}{'' if n == 1 else 's'}" for k, n in sorted(counts.items()) if k in words
    ]
    return parse.Parsed("read", "model", {**counts, "text": ", ".join(parts) or "Read"}, refs)


def summary_line(up: Upload) -> str:
    if up.status in ("added", "reading"):
        return "Reading…"
    return str((up.summary or {}).get("text", ""))
