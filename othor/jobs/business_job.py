# SPDX-License-Identifier: Apache-2.0
"""Draft the business message from the uploads (2d), with the model only. payload: {}"""

from __future__ import annotations

import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext
from othor.setup import business
from othor.setup.inputs import gather


def run(ctx: WorkerContext, job: Job) -> None:
    cid = job.company_id
    with ctx.sf() as s, tenant_scope(cid):
        inp = gather(s)
        company = s.scalars(sa.select(Company).where(Company.id == cid)).first()
        name = company.name if company else ""
    got = business.draft(ctx.gateway, cid, name, inp.refs)
    with ctx.sf() as s, tenant_scope(cid):
        row = business.message(s)
        if got is not None:
            paras, missing = got
            row.draft_text = "\n\n".join(paras) or None
            row.not_covered = missing
        s.commit()
