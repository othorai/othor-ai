# SPDX-License-Identifier: Apache-2.0
"""The run ledger: input, output and run hashes, chained run to run (docs/02, docs/03).

- input_hash  = sha256 of the run's canonical `inputs/manifest.json`, which names the spec hash,
  the run date, kind and scope, and the canonical row hash of every input file;
- output_hash = sha256 of the canonical `outputs/outputs.json`;
- run_hash    = sha256(prev_run_hash | number | kind | spec hash | image | input | output).

Timestamps, durations and run ids never enter the manifest or the outputs, so two runs on the
same inputs have the same input and output hashes; their run hashes differ because the chain
moves on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.config import Settings
from othor.engine.models import Run
from othor.engine.store import RunFiles
from othor.ontology.models import Spec

SEALED = ("done", "partial")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_hash(
    prev: str | None,
    number: int,
    kind: str,
    spec_hash: str,
    image_version: str,
    input_hash: str,
    output_hash: str,
) -> str:
    parts = [prev or "", str(number), kind, spec_hash, image_version, input_hash, output_hash]
    return sha("|".join(parts).encode())


def last_sealed(s: Session, before: int | None = None) -> Run | None:
    q = sa.select(Run).where(Run.status.in_(SEALED), Run.run_hash.is_not(None))
    if before is not None:
        q = q.where(Run.number < before)
    return s.scalars(q.order_by(Run.number.desc()).limit(1)).first()


@dataclass
class Verify:
    runs: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify(s: Session, settings: Settings, company_id: object) -> Verify:
    """Walk the chain inside the company's tenant scope; recheck every stored hash."""
    out = Verify()
    prev: str | None = None
    runs = s.scalars(sa.select(Run).where(Run.status.in_(SEALED)).order_by(Run.number)).all()
    for r in runs:
        out.runs += 1
        label = f"#{r.number:04d}"
        if r.prev_run_hash != prev:
            out.problems.append(f"{label}: chain broken (previous hash doesn't match)")
        spec = s.scalars(sa.select(Spec).where(Spec.id == r.spec_id)).one()
        files = RunFiles(settings.runs_dir(r.company_id), r.id)
        if not files.manifest.exists() or not files.outputs_json.exists():
            out.problems.append(f"{label}: run files missing")
        else:
            if sha(files.manifest.read_bytes()) != r.input_hash:
                out.problems.append(f"{label}: inputs changed since the run")
            if sha(files.outputs_json.read_bytes()) != r.output_hash:
                out.problems.append(f"{label}: outputs changed since the run")
        expect = run_hash(
            r.prev_run_hash,
            r.number,
            r.kind,
            spec.hash,
            r.image_version,
            r.input_hash or "",
            r.output_hash or "",
        )
        if expect != r.run_hash:
            out.problems.append(f"{label}: run hash doesn't match its parts")
        prev = r.run_hash
    return out


def step_label(name: str) -> str:
    """'read:sales_dw' → 'reading sales_dw' (4h, 4e1, the 8c email)."""
    kind, _, src = name.partition(":")
    verb = {
        "read": "reading",
        "materialise": "storing",
        "backfill": "back-filling",
        "probes": "checking",
        "carry": "carrying over",
    }.get(kind)
    if verb and src:
        return f"{verb} {src}"
    # the steps with no source, in the owner's words (4e1, M12b): never "derive"
    return {
        "derive": "computing the baselines",
        "detect": "running the detectors",
        "forecast": "forecasting",
        "brief": "writing the briefs",
        "seal": "sealing the run",
    }.get(name, name)
