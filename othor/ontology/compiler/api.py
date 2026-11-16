# SPDX-License-Identifier: Apache-2.0
"""Compile an Ontology version into a stored, hashed, immutable spec."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.events import SYSTEM_ACTOR, record_event
from othor.core.models import Company
from othor.ontology import content, service
from othor.ontology.compiler.build import COMPILER_VERSION, compile_records, reject_reason
from othor.ontology.compiler.catalog import load_catalog
from othor.ontology.compiler.render import CompileError
from othor.ontology.models import OntologyVersion, Spec


def canonical(doc: Any) -> str:
    """Sorted keys, no whitespace, exact numbers: the bytes the hash is taken over."""

    def default(o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        if hasattr(o, "isoformat"):
            return o.isoformat()
        raise TypeError(f"not canonical JSON: {type(o).__name__}")

    def check(o: Any) -> None:
        if isinstance(o, float):
            raise TypeError("floats are not allowed in a spec (use decimal strings)")
        if isinstance(o, dict):
            for v in o.values():
                check(v)
        elif isinstance(o, list):
            for v in o:
                check(v)

    check(doc)
    return json.dumps(
        doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default
    )


def spec_hash(doc: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(doc).encode()).hexdigest()


def company_settings(c: Company) -> dict[str, Any]:
    return {
        "fiscal_year_start_month": c.fiscal_year_start_month,
        "timezone": c.timezone,
        "currency": c.currency,
        "number_format": c.number_format,
        "week_starts": "monday",
    }


DECIDED = "othor_ai"  # source_ref["decided_by"] on a record Let Othor AI decide applied


def decided_families(s: Session, v: int) -> list[str]:
    """The Sweep families that read a record decided by Othor AI and not yet confirmed (v0.12,
    docs/plans/let-othor-ai-decide.md): their findings are capped at Assessed · Medium."""
    out = set()
    for r in service.active_at(s, v):
        if (r.source_ref or {}).get("decided_by") != DECIDED:
            continue
        kind = (r.body or {}).get("kind")
        if r.type == "rule" and kind in ("pair", "reconcile"):
            out.add(f"{kind}:{r.key}")
        elif r.type == "rule" and kind == "threshold":
            out.add(f"process_artefact:{r.key}")
        elif r.type == "breakdown":
            out.add(f"entity:{r.key}")
        elif r.type == "metric":
            out.add(f"bimodal_average:{r.key}")
    return sorted(out)


def compile_version(
    s: Session, company: Company, version: int | None = None, *, actor: str = SYSTEM_ACTOR
) -> Spec:
    """Compile the live records at `version` (default: the latest). An unchanged version
    returns its existing spec for this compiler; the spec table is append-only, so a new compiler
    adds a spec for the same version and earlier runs keep the one they ran."""
    v = version if version is not None else service.current_number(s)
    row = (
        s.scalars(sa.select(OntologyVersion).where(OntologyVersion.number == v)).first()
        if v
        else None
    )
    if (
        row is not None
        and v > 1
        and content.is_citation_only(row)
        and decided_families(s, v) == decided_families(s, v - 1)
    ):
        # "Source updated" (M10b): the same records, so the same spec as the version before
        return compile_version(s, company, v - 1, actor=actor)
    existing = s.scalars(
        sa.select(Spec).where(Spec.ontology_version == v, Spec.compiler_version == COMPILER_VERSION)
    ).first()
    if existing is not None:
        return existing
    records = {(r.type, r.key): r.body for r in service.active_at(s, v)} if v else {}
    try:
        doc = compile_records(records, load_catalog(s), company_settings(company), v)
        decided = decided_families(s, v) if v else []
        if decided:  # only then: a company without one compiles the same spec as before
            doc["decided_by_ai"] = decided
    except CompileError as e:
        record_event(
            s,
            actor=actor,
            action="spec.rejected",
            object_type="ontology_version",
            object_id=str(v),
            after=reject_reason(e),
        )
        raise
    h = spec_hash(doc)
    spec = Spec(
        ontology_version=v,
        compiler_version=COMPILER_VERSION,
        compiled=doc,
        hash=h,
        content_hash=content.compiled_hash(doc),
    )
    s.add(spec)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="spec.compiled",
        object_type="spec",
        object_id=spec.id,
        after={"ontology_version": v, "hash": h, "metrics": len(doc["metrics"])},
    )
    return spec
