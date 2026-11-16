# SPDX-License-Identifier: Apache-2.0
"""Runs and their ledger (docs/03 "Runs and results"). Series values live in DuckDB; the metadata
DB keeps what each run was, its steps, and one row per series with its history and staleness."""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from othor.core.models import Base, IdMixin, TenantMixin

# fixture: test environment only; rerun: Run again now (v0.11), the nightly steps on demand
RUN_KINDS = ("dry", "nightly", "manual", "fixture", "rerun")
# the kinds that run Sweep and write briefs: what Sweep, Home and the brief pages read
DETECTING_KINDS = ("nightly", "dry", "rerun")
RUN_STATUSES = ("running", "done", "partial", "failed")


class Run(IdMixin, TenantMixin, Base):
    """One run. Its hashes are written once, at `seal`, and chain to the previous run's."""

    __tablename__ = "run"
    __table_args__ = (sa.UniqueConstraint("company_id", "number"),)

    number: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(sa.String(16))  # dry · nightly · manual · rerun
    scope: Mapped[dict[str, Any] | None] = mapped_column()  # None = company; {"source_id": …}
    run_date: Mapped[dt.date] = mapped_column(sa.Date)  # sees data up to run_date − 1
    spec_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("spec.id"))
    image_version: Mapped[str] = mapped_column(sa.String(32))
    started_at: Mapped[datetime] = mapped_column()
    ended_at: Mapped[datetime | None] = mapped_column()
    status: Mapped[str] = mapped_column(sa.String(16), default="running")
    input_hash: Mapped[str | None] = mapped_column(sa.String(64))
    output_hash: Mapped[str | None] = mapped_column(sa.String(64))
    prev_run_hash: Mapped[str | None] = mapped_column(sa.String(64))
    run_hash: Mapped[str | None] = mapped_column(sa.String(64))
    job_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)


class RunStep(IdMixin, TenantMixin, Base):
    __tablename__ = "run_step"
    __table_args__ = (sa.UniqueConstraint("run_id", "name"),)

    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("run.id"), index=True)
    name: Mapped[str] = mapped_column(sa.String(80))
    ordinal: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(
        sa.String(16), default="queued"
    )  # queued running done failed
    started_at: Mapped[datetime | None] = mapped_column()
    ended_at: Mapped[datetime | None] = mapped_column()
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(sa.String(300))


class SeriesMeta(IdMixin, TenantMixin, Base):
    """One row per metric × grain × breakdown ('' = total): docs/03 `series`."""

    __tablename__ = "series_meta"
    __table_args__ = (sa.UniqueConstraint("company_id", "metric", "grain", "breakdown"),)

    metric: Mapped[str] = mapped_column(sa.String(100))
    grain: Mapped[str] = mapped_column(sa.String(8))
    breakdown: Mapped[str] = mapped_column(sa.String(64), default="")
    source_kind: Mapped[str] = mapped_column(sa.String(16), default="inside")
    stale: Mapped[bool] = mapped_column(default=False)
    last_good_at: Mapped[dt.date | None] = mapped_column(sa.Date)
    history_start: Mapped[dt.date | None] = mapped_column(sa.Date)
    history_days: Mapped[int] = mapped_column(default=0)
    reconstructed_to: Mapped[dt.date | None] = mapped_column(sa.Date)
    reconstructed_from: Mapped[str | None] = mapped_column(sa.String(120))
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)


class Finding(IdMixin, TenantMixin, Base):
    """One finding of one run (docs/03): written at `seal` from the run's outputs, so Sweep's
    counts and every later screen read one run. Open and recently resolved ones are listed."""

    __tablename__ = "finding"
    __table_args__ = (sa.UniqueConstraint("run_id", "fingerprint"),)

    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("run.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(sa.String(32), index=True)
    detector: Mapped[str] = mapped_column(sa.String(40))
    metric: Mapped[str] = mapped_column(sa.String(100))
    breakdown: Mapped[str] = mapped_column(sa.String(64), default="")
    breakdown_value: Mapped[str | None] = mapped_column(sa.String(200))
    state: Mapped[str] = mapped_column(
        sa.String(24)
    )  # measured assessed not_determinable needs_confirmation
    confidence: Mapped[str | None] = mapped_column(sa.String(8))  # high · medium
    money: Mapped[str | None] = mapped_column(sa.String(32))
    status: Mapped[str] = mapped_column(sa.String(16))  # open · resolved (snoozed, muted: M6b)
    change: Mapped[str] = mapped_column(
        sa.String(24)
    )  # New · Worse · Better · Open · day N · Resolved
    first_seen_run: Mapped[int] = mapped_column()
    days_open: Mapped[int] = mapped_column()
    series_version: Mapped[str | None] = mapped_column(sa.String(16))
    body: Mapped[dict[str, Any]] = mapped_column(default=dict)  # the finding as in outputs


class DetectorResult(IdMixin, TenantMixin, Base):
    """Every test of the run (docs/03): passed or not, and why not."""

    __tablename__ = "detector_result"

    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("run.id"), index=True)
    detector: Mapped[str] = mapped_column(sa.String(40))
    version: Mapped[str] = mapped_column(sa.String(8))
    series: Mapped[list[str]] = mapped_column(sa.JSON)
    statistic: Mapped[str | None] = mapped_column(sa.String(40))
    p_value: Mapped[str | None] = mapped_column(sa.String(40))
    q_value: Mapped[str | None] = mapped_column(sa.String(40))
    effect: Mapped[str | None] = mapped_column(sa.String(40))
    money: Mapped[str | None] = mapped_column(sa.String(32))
    passed: Mapped[bool] = mapped_column(default=False)
    reason_if_not: Mapped[str | None] = mapped_column(sa.String(200))
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Watch(IdMixin, TenantMixin, Base):
    """An indicator, a threshold, an owner (docs/03 `watch`); checked every night."""

    __tablename__ = "watch"

    label: Mapped[str] = mapped_column(sa.String(200))
    indicator: Mapped[dict[str, Any]] = mapped_column()
    threshold: Mapped[str] = mapped_column(sa.String(32))
    direction: Mapped[str] = mapped_column(sa.String(8))  # above · below
    owner_name: Mapped[str] = mapped_column(sa.String(120))
    created_from_finding: Mapped[str | None] = mapped_column(sa.String(32))
    since: Mapped[dt.date] = mapped_column(sa.Date)
    status: Mapped[str] = mapped_column(sa.String(16), default="within")
    breached_since: Mapped[dt.date | None] = mapped_column(sa.Date)
    last_value: Mapped[str | None] = mapped_column(sa.String(32))
    stopped_at: Mapped[datetime | None] = mapped_column()


class Snooze(IdMixin, TenantMixin, Base):
    """Snooze 7 days (docs/03 `snooze`): a finding's fingerprint, out of Findings until a date.
    The page applies it at once (the overlay); the engine from the next run. Undo ends it."""

    __tablename__ = "snooze"

    fingerprint: Mapped[str] = mapped_column(sa.String(32), index=True)
    until: Mapped[dt.date] = mapped_column(sa.Date)
    label: Mapped[str] = mapped_column(sa.String(300), default="")
    actor: Mapped[str] = mapped_column(sa.String(120))
    ended_at: Mapped[datetime | None] = mapped_column()


class Mute(IdMixin, TenantMixin, Base):
    """Mute (docs/03 `mute`): a pattern (detector + series + key entities, any direction) that
    Sweep stops showing. `fingerprint` is the finding it was muted from. Undo ends it."""

    __tablename__ = "mute"

    pattern: Mapped[str] = mapped_column(sa.String(64), index=True)
    fingerprint: Mapped[str] = mapped_column(sa.String(32))
    label: Mapped[str] = mapped_column(sa.String(300), default="")
    actor: Mapped[str] = mapped_column(sa.String(120))
    ended_at: Mapped[datetime | None] = mapped_column()


class Proposal(IdMixin, TenantMixin, Base):
    """A proposal for the Ontology from a rule (docs/03 `proposal`, docs/04 §10). Home shows
    them from M10; a declined one is never proposed again for the same fingerprint."""

    __tablename__ = "proposal"
    __table_args__ = (sa.UniqueConstraint("company_id", "fingerprint"),)

    kind: Mapped[str] = mapped_column(sa.String(24))  # suppression · definition_change · goal
    text: Mapped[str] = mapped_column(sa.String(500))
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    origin: Mapped[dict[str, Any]] = mapped_column(default=dict)
    fingerprint: Mapped[str] = mapped_column(sa.String(64))
    status: Mapped[str] = mapped_column(sa.String(16), default="open")  # open accepted declined
    run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    decided_at: Mapped[datetime | None] = mapped_column()
    decided_by: Mapped[str | None] = mapped_column(sa.String(64))  # M10a
    applied_version: Mapped[int | None] = mapped_column()  # the Ontology version Accept wrote
    undone_at: Mapped[datetime | None] = mapped_column()


class Forecast(IdMixin, TenantMixin, Base):
    """A forecast stored before its actual exists (docs/03 `forecast`, M7): written at seal by
    the run that made it; Forecast breach compares a complete period with the forecast of
    record, the latest one made before the period started. `reconstructed` only in tests."""

    __tablename__ = "forecast"

    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("run.id"), index=True)
    series: Mapped[str] = mapped_column(sa.String(120), index=True)  # metric|grain
    metric: Mapped[str] = mapped_column(sa.String(100))
    grain: Mapped[str] = mapped_column(sa.String(8))
    period: Mapped[dt.date] = mapped_column(sa.Date, index=True)
    as_of: Mapped[dt.date] = mapped_column(sa.Date)
    method: Mapped[str] = mapped_column(sa.String(24))
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    point: Mapped[str] = mapped_column(sa.String(40))
    lower80: Mapped[str | None] = mapped_column(sa.String(40))
    upper80: Mapped[str | None] = mapped_column(sa.String(40))
    reconstructed: Mapped[bool] = mapped_column(default=False)
    written_at: Mapped[datetime] = mapped_column()
