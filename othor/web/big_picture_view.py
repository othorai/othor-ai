# SPDX-License-Identifier: Apache-2.0
"""Big Picture (v0.19, M19.5) for the web: the latest sealed run's computed results as facts, the
one model call, the stored report. Made once per run and language, on demand, never by the run
(rule 4); the report changes nothing, so there's no Apply (rule 6). Pure parts: briefs.big_picture.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.briefs import big_picture as bp
from othor.briefs.models import BigPicture, Brief, BriefIssue
from othor.core.config import Settings
from othor.core.models import Company
from othor.engine.models import Run

FINDINGS = 8  # Sweep's open findings beyond the briefs' own, at most


def available(settings: Settings, s: Session, now: Any, provider: Any) -> bool:
    """A model to write with: configured, and not marked down by a recent failed call."""
    from othor.ai.gateway import model_status

    if not getattr(provider, "configured", False):
        return False
    return model_status(settings, s, now)[1] != "down"


def stored(s: Session, run: Run, language: str) -> BigPicture | None:
    return s.scalars(
        sa.select(BigPicture).where(
            BigPicture.run_id == run.id,
            BigPicture.language == language,
            BigPicture.deleted_at.is_(None),
        )
    ).first()


def gather(s: Session, settings: Settings, run: Run, today: dt.date) -> bp.Facts:
    """The run's computed results as facts: each active brief's issue, Sweep's other open
    findings, and what the briefs' headline numbers mean (the Ontology's words)."""
    from othor.web import sweep_view as sv

    briefs = s.scalars(
        sa.select(Brief)
        .where(Brief.status == "active", Brief.deleted_at.is_(None))
        .order_by(Brief.position, Brief.name)
    ).all()
    out, seen, headline = [], set(), []
    for b in briefs:
        # this run's own issue; else the newest before it. A rerun writes another issue for the
        # same day, so the date alone can pick an older run's issue (15 Nov 2026, indigo2)
        issue = (
            s.scalars(
                sa.select(BriefIssue).where(
                    BriefIssue.brief_id == b.id,
                    BriefIssue.status == "ready",
                    BriefIssue.run_id == run.id,
                )
            ).first()
            or s.scalars(
                sa.select(BriefIssue)
                .where(BriefIssue.brief_id == b.id, BriefIssue.status == "ready")
                .where(BriefIssue.date <= run.run_date)
                .order_by(BriefIssue.date.desc(), BriefIssue.created_at.desc())
                .limit(1)
            ).first()
        )
        if issue is None:
            continue
        body = dict(issue.body)
        out.append({"name": b.name, "href": f"/brief/{b.id}", "body": body})
        seen |= {i.get("fingerprint") for i in body.get("items") or []}
        headline += [t.get("metric") for t in body.get("tiles") or [] if t.get("metric")]
    findings: list[dict[str, Any]] = []
    spec: dict[str, Any] = {}
    rv = sv.load(s, settings, run, today)
    if rv is not None:
        spec = rv.compiled
        for c in sv.finding_rows(rv, s):
            if c["fp"] not in seen and len(findings) < FINDINGS:
                findings.append(
                    {
                        "fp": c["fp"],
                        "title": c["title"],
                        "line": c["line"],
                        "money": c["money"],
                        "state": c["state"],
                    }
                )
    metrics = [
        {"key": k, "name": m.get("name") or k, "description": m.get("description") or ""}
        for k, m in sorted((spec.get("metrics") or {}).items())
        if k in set(headline)
    ]
    names = {k: str(m.get("name") or k) for k, m in (spec.get("metrics") or {}).items()}
    return bp.facts(out, findings, metrics, names)


def make(
    s: Session,
    gateway: Any,
    company: Company,
    run: Run,
    language: str,
    f: bp.Facts,
    actor: str | uuid.UUID,
) -> tuple[BigPicture | None, str]:
    """One model call, guarded and filled; stored when enough survived. (row, guard)."""
    from othor.ai.gateway import input_hash
    from othor.core.events import record_event

    inputs = bp.model_input(f.inputs, language)
    got = gateway.complete(company.id, bp.PROMPT, bp.VERSION, inputs)
    if not got.ok:
        return None, got.guard
    rep = bp.fill(got.output, f, language)
    if rep.body is None:
        return None, rep.guard
    row = BigPicture(
        company_id=company.id,
        workspace_id=company.workspace_id or company.id,
        run_id=run.id,
        language=language,
        body={
            **rep.body,
            "run": run.number,
            "run_date": run.run_date.isoformat(),
            "spec": _spec_version(s, run),
        },
        prompt=f"{bp.PROMPT}@{bp.VERSION}",
        model_id=_model_id(s, bp.PROMPT, input_hash(bp.PROMPT, bp.VERSION, inputs, [[], []])),
        input_hash=input_hash(bp.PROMPT, bp.VERSION, inputs, [[], []]),
        guard=rep.guard,
    )
    s.add(row)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="big_picture.made",
        object_type="big_picture",
        object_id=row.id,
        after={"run": run.number, "language": language, "guard": rep.guard},
    )
    s.commit()
    return row, rep.guard


def _spec_version(s: Session, run: Run) -> int | None:
    from othor.ontology.models import Spec

    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).first()
    return spec.ontology_version if spec is not None else None


def _model_id(s: Session, prompt: str, h: str) -> str:
    from othor.ai.models import ModelCall

    row = s.scalars(
        sa.select(ModelCall)
        .where(ModelCall.prompt_id == prompt, ModelCall.input_hash == h)
        .order_by(ModelCall.created_at.desc())
        .limit(1)
    ).first()
    return row.model_id if row is not None else ""


def _legend(spec: Any) -> tuple[Any, list[dict[str, str]]]:
    """A finding's chart for Big Picture: series names and the band in a legend above, not on the
    plot (owner, 15 Nov 2026); plain words, the technical part left to the finding (v0.2 focus)."""
    import dataclasses

    items = [
        {"name": s.name, "role": s.role, "mark": "line"} for s in spec.series if spec.kind == "line"
    ]
    if spec.band is not None and spec.kind == "line":
        items.append(
            {
                "name": "Spread" if spec.band.role == "negative" else "Likely range",
                "role": spec.band.role,
                "mark": "area",
            }
        )
    marks = tuple(
        dataclasses.replace(a, label=_cap(a.label.split(" · ")[0]))
        if a.type == "region" and a.label
        else a
        for a in spec.annotations
    )
    return dataclasses.replace(spec, annotations=marks), items


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def range_spec(c: dict[str, Any]) -> Any:
    """A forecast card's range bar, as a ChartSpec (`briefs.ahead.range_chart`)."""
    from othor.briefs.ahead import range_chart

    return range_chart(c)


def report(row: BigPicture, company_name: str = "") -> dict[str, Any]:
    """The stored report for the panel and the email: headings and header in its language, the
    charts as ChartSpecs with their legends."""
    from othor.core import fmt
    from othor.engine.finding_charts import load

    body = dict(row.body)
    lang = row.language if row.language in bp.HEADINGS else "en"
    findings = []
    for c in body.get("findings") or []:
        spec, legend = _legend(load(c["chart"])) if c.get("chart") else (None, [])
        findings.append({**c, "spec": spec, "legend": legend})
    run_date = body.get("run_date")
    return {
        "id": str(row.id),
        "language": lang,
        "lang_attr": lang,
        "header": bp.HEADER[lang],
        "h": bp.HEADINGS[lang],
        "subtitle": " · ".join(
            x
            for x in (
                f"From the run of {fmt.date_short(dt.date.fromisoformat(run_date))}"
                if run_date
                else "",
                company_name,
            )
            if x
        ),
        "one_line": body.get("one_line") or [],
        "summary": body.get("summary") or [],
        "kpis": body.get("kpis") or [],
        "findings": findings,
        "stakes_note": body.get("stakes_note"),
        "stakes": body.get("stakes") or [],
        "look_first": body.get("look_first") or [],
        "forecasts": [{**c, "range": range_spec(c)} for c in body.get("forecasts") or []],
        "words": body.get("words") or [],
        "footer": " · ".join(
            x
            for x in (
                f"Run #{int(body['run']):04d}" if body.get("run") else "",
                f"spec v{body['spec']}" if body.get("spec") else "",
                row.prompt,
                row.model_id,
            )
            if x
        ),
        "made": row.created_at,
    }


def find(s: Session, rid: str) -> BigPicture | None:
    try:
        key = uuid.UUID(rid)
    except ValueError:
        return None
    return s.scalars(
        sa.select(BigPicture).where(BigPicture.id == key, BigPicture.deleted_at.is_(None))
    ).first()


def panel(
    s: Session, settings: Settings, company: Company, now: Any, provider: Any, extended: bool
) -> dict[str, Any]:
    """Home's side panel (owner, 15 Nov 2026): the stored report, else the writing state while a
    model can write it, else "Not available right now". Normal (680 px) or extended."""
    from othor.core import fmt
    from othor.web import home_view as hv

    run = hv.latest_run(s)
    lang = company.report_language if company.report_language in bp.HEADINGS else "en"
    row = stored(s, run, lang) if run is not None else None
    return {
        "as_of": " · ".join(
            x
            for x in (
                f"From the run of {fmt.date_short(run.run_date)}" if run is not None else "",
                company.name,
            )
            if x
        ),
        "report": report(row, company.name) if row is not None else None,
        "writing": row is None and run is not None and available(settings, s, now, provider),
        "extended": extended,
    }
