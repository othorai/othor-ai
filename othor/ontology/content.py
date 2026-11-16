# SPDX-License-Identifier: Apache-2.0
"""Content hashes (M10b, decided 6 Nov 2026).

- `records_hash`: a version's meaning: its live records as [type, key, body], sorted, without the
  version number, time, author, reason or citations (`source_ref`). Stored on `ontology_version`.
  It also covers the records that never compile (business facts, Names and words, owners).
- `compiled_hash`: a spec's meaning: the compiled document without its `ontology_version`.
  Stored on `spec`. `spec.hash` stays the version's identity (with its number).

A Restore gives a new number with the same content; a citation-only change ("Source updated")
keeps the records hash and reuses the current spec.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ontology.models import OntologyVersion


def _canonical(doc: Any) -> str:
    from othor.ontology.compiler.api import canonical

    return canonical(doc)


def records_hash(rows: Iterable[tuple[str, str, dict[str, Any]]]) -> str:
    items = sorted([t, k, b] for t, k, b in rows)
    return hashlib.sha256(_canonical({"records": items}).encode()).hexdigest()


def compiled_hash(doc: dict[str, Any]) -> str:
    body = {k: v for k, v in doc.items() if k != "ontology_version"}
    return hashlib.sha256(_canonical(body).encode()).hexdigest()


def version_hash(s: Session, number: int) -> str:
    from othor.ontology import service

    return records_hash((r.type, r.key, r.body) for r in service.active_at(s, number))


def is_citation_only(v: OntologyVersion) -> bool:
    changes = (v.diff or {}).get("changes") or []
    return bool(changes) and all(c.get("op") == "cite" for c in changes)


def same_as(s: Session, v: OntologyVersion) -> int | None:
    """The latest earlier version with the same content ("Same content as v3"), or None."""
    if not v.content_hash:
        return None
    n: int | None = s.scalar(
        sa.select(sa.func.max(OntologyVersion.number)).where(
            OntologyVersion.number < v.number, OntologyVersion.content_hash == v.content_hash
        )
    )
    return n


def spec_content_hash(spec: Any) -> str:
    """A spec's compiled hash: stored from 0029 on, computed for older (append-only) rows."""
    return str(spec.content_hash or compiled_hash(spec.compiled))
