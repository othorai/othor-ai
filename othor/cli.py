# SPDX-License-Identifier: Apache-2.0
"""`othor` command: migrate, seed, web, worker, run, resume, replay, ledger, export-spec, admin."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Annotated

import typer

from othor import __version__
from othor.core.config import get_settings
from othor.core.crypto import InstanceKey

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Othor AI")


def _not_yet(milestone: str) -> None:
    typer.echo(f"Not built yet: arrives in {milestone} (docs/10-milestones.md).", err=True)
    raise typer.Exit(2)


@app.command()
def version() -> None:
    """Print the image version."""
    typer.echo(__version__)


@app.command()
def migrate(
    create_key: Annotated[
        bool,
        typer.Option(
            "--create-key",
            help="Self-host first start: create the instance key if it's missing and no company "
            "exists yet (never over a database that already holds companies)",
        ),
    ] = False,
) -> None:
    """Upgrade the metadata DB to the latest schema and make sure the instance key exists."""
    import sqlalchemy as sa

    from othor.core.db import make_engine
    from othor.core.models import Company
    from othor.migrations.runner import upgrade

    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    allow = s.env != "prod"
    if create_key and not s.instance_key_path.exists():
        # a fresh install (M20.4): the key is new because nothing was ever encrypted with it. A
        # database with companies but no key means the data volume was lost: restore it instead.
        upgrade(s.db_url)
        engine = make_engine(s.db_url)
        with engine.connect() as c:
            n = c.execute(sa.select(sa.func.count()).select_from(Company.__table__)).scalar_one()
        engine.dispose()
        if n:
            typer.echo(
                "the instance key is missing but companies exist: restore /data from a backup "
                "(docs/self-host.md, Backups); not creating a new key",
                err=True,
            )
            raise typer.Exit(1)
        allow = True
    InstanceKey.load_or_create(s.instance_key_path, allow_create=allow)
    upgrade(s.db_url)
    typer.echo("metadata DB at head")


@app.command()
def seed(
    as_of: Annotated[str, typer.Option(help="Data up to this date (YYYY-MM-DD)")] = "2026-11-04",
    seed_value: Annotated[int, typer.Option("--seed", help="Generator seed")] = 20261101,
    scale: Annotated[
        str, typer.Option(help="default (fast, ~20k invoices) or large (perf + 4,000-table scan)")
    ] = "default",
    mysql_url: Annotated[
        str, typer.Option(envvar="OTHOR_SAMPLE_MYSQL_URL")
    ] = "mysql+pymysql://root:root_dev@localhost:3307/erp_db",
    pg_url: Annotated[
        str, typer.Option(envvar="OTHOR_SAMPLE_PG_URL")
    ] = "postgresql+psycopg://dw_admin:dw_admin_dev@localhost:5443/sales_dw",
) -> None:
    """Dev only: generate the Indigo Thread sample company into erp_db, sales_dw and Sheets.

    Writes the Sheets fixtures too.
    """
    try:
        from tests.fixtures.sample_company import generate, load
    except ImportError:
        typer.echo("seed is dev-only: tests/fixtures is not in this image", err=True)
        raise typer.Exit(2) from None
    s = get_settings()
    company = generate.generate(seed=seed_value, as_of=dt.date.fromisoformat(as_of))
    if scale not in ("default", "large"):
        raise typer.BadParameter("--scale is default or large")
    counts = load.load_all(
        company,
        mysql_url=mysql_url,
        pg_url=pg_url,
        sheets_dir=s.sample_dir / "sheets",
        scale=scale,
    )
    for source in ("erp_db", "sales_dw", "sheets"):
        mine = {k: v for k, v in counts.items() if k.startswith(source + ".")}
        typer.echo(f"{source:10s} {len(mine):>4} tables/tabs {sum(mine.values()):>10,} rows")
    typer.echo(
        f"seeded Indigo Thread ({scale}) as of {as_of} · data hash {company.data_hash()[:12]}"
    )


@app.command()
def web(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Serve the web UI."""
    import uvicorn

    from othor.core.logging import configure_logging

    configure_logging()  # uvicorn's access log goes through the redacting handler
    uvicorn.run(
        "othor.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        reload_dirs=["othor"] if reload else None,
        log_config=None,
    )


@app.command()
def worker(once: bool = False) -> None:
    """Run jobs (scans, reads, setup drafts, runs) and the scheduler that queues the 02:00 runs."""
    from othor.ai.gateway import ModelGateway, provider_for
    from othor.core.crypto import InstanceKey
    from othor.core.db import make_engine, make_session_factory
    from othor.core.logging import configure_logging
    from othor.jobs.runner import WorkerContext, run_pending, worker_loop

    configure_logging()
    s = get_settings()
    sf = make_session_factory(make_engine(s.db_url, pool_pre_ping=True))
    key = InstanceKey.load_or_create(s.instance_key_path, allow_create=s.env != "prod")
    ctx = WorkerContext(
        s, sf, key, gateway=ModelGateway(sf, provider_for(s), monthly_tokens=s.ai_monthly_tokens)
    )
    if once:
        typer.echo(f"{run_pending(ctx)} jobs run")
        return
    worker_loop(ctx)


@app.command()
def run(
    company: Annotated[str, typer.Option()],
    date: Annotated[str, typer.Option(help="Run date D: sees data up to D − 1")],
    kind: Annotated[str, typer.Option(help="nightly · dry · manual")] = "nightly",
) -> None:
    """Run one company's run for a date, in this process; print its number, status and hashes."""
    from othor.core.tenancy import tenant_scope
    from othor.engine import orchestrator

    ctx, c = _engine_ctx(company)
    with ctx.sf() as s, tenant_scope(c.id):
        try:
            r = orchestrator.start(
                s,
                c,
                kind=kind,
                run_date=dt.date.fromisoformat(date),
                now=ctx.clock(),
                image_version=ctx.image_version,
            )
        except orchestrator.NoSpec as e:
            typer.echo(f"Nothing to run: {e}.", err=True)
            raise typer.Exit(1) from e
        s.commit()
        run_id = r.id
        typer.echo(f"#{r.number:04d} started · run {run_id}")
    with tenant_scope(c.id):
        status = orchestrator.execute(ctx, run_id)
    _print_run(ctx, c.id, run_id, status)


@app.command()
def resume(run_id: str) -> None:
    """Resume a run that stopped (a killed process): the steps already done are skipped."""
    import uuid

    import sqlalchemy as sa

    from othor.core.tenancy import system_scope, tenant_scope
    from othor.engine import orchestrator
    from othor.engine.models import Run

    ctx, _ = _engine_ctx(None)
    with ctx.sf() as s, system_scope("cli: resume a run"):
        r = s.scalars(sa.select(Run).where(Run.id == uuid.UUID(run_id))).first()
        if r is None:
            typer.echo(f"No run {run_id}.", err=True)
            raise typer.Exit(1)
        cid = r.company_id
    with tenant_scope(cid):
        status = orchestrator.execute(ctx, uuid.UUID(run_id))
    _print_run(ctx, cid, uuid.UUID(run_id), status)


@app.command()
def replay(run_id: str) -> None:
    """Re-execute a run from its snapshot and diff outputs byte for byte (exit 1 on a diff)."""
    import uuid

    import sqlalchemy as sa

    from othor.core.tenancy import system_scope, tenant_scope
    from othor.engine.models import Run
    from othor.engine.replay import replay as do_replay

    ctx, _ = _engine_ctx(None)
    with ctx.sf() as s, system_scope("cli: replay a run"):
        r = s.scalars(sa.select(Run).where(Run.id == uuid.UUID(run_id))).first()
        if r is None:
            typer.echo(f"No run {run_id}.", err=True)
            raise typer.Exit(1)
        cid = r.company_id
    with tenant_scope(cid):
        out = do_replay(ctx, uuid.UUID(run_id))
    if out.ok:
        typer.echo(f"#{out.number:04d} replayed: identical · {out.replayed_hash}")
        return
    typer.echo(f"#{out.number:04d} replay differs:", err=True)
    for line in out.diff[:50]:
        typer.echo(f"  {line}", err=True)
    raise typer.Exit(1)


ledger = typer.Typer(help="The run ledger")
app.add_typer(ledger, name="ledger")


@ledger.command("verify")
def ledger_verify(company: Annotated[str, typer.Option()]) -> None:
    """Walk a company's run chain and recheck every stored hash."""
    from othor.core.tenancy import tenant_scope
    from othor.engine.ledger import verify

    ctx, c = _engine_ctx(company)
    with ctx.sf() as s, tenant_scope(c.id):
        v = verify(s, ctx.settings, c.id)
    if v.ok:
        typer.echo(f"{v.runs} runs · chain intact")
        return
    for p in v.problems:
        typer.echo(p, err=True)
    raise typer.Exit(1)


admin = typer.Typer(
    help="Operator commands: reports across every company (read-only) and invitations",
    no_args_is_help=True,
)
app.add_typer(admin, name="admin")

_AdminCompany = Annotated[str | None, typer.Option("--company", help="One company's slug")]
_AdminDays = Annotated[int, typer.Option(help="Window in days, ending now")]
_AdminJson = Annotated[bool, typer.Option("--json", help="Print JSON instead of tables")]


def _admin_session() -> Session:
    from othor.core.db import make_engine, make_session_factory

    return make_session_factory(make_engine(get_settings().db_url, pool_pre_ping=True))()


def _admin_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _cell(v: object) -> str:
    from othor.core import fmt

    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, dt.datetime):
        from zoneinfo import ZoneInfo

        local = v.astimezone(ZoneInfo("Asia/Kolkata"))  # clock()'s default display zone
        return f"{fmt.date_short(local.date())} {fmt.clock(v)}"
    if isinstance(v, dt.date):
        return fmt.date_short(v)
    if isinstance(v, int):
        return fmt.num(v)
    return str(v)


def _mb(n: int) -> str:
    from othor.core import fmt

    return f"{fmt.num(n / 1_048_576, decimals=1)} MB"


def _table(title: str, rows: list[dict[str, object]], cols: list[tuple[str, str]]) -> None:
    typer.echo(f"\n{title}")
    if not rows:
        typer.echo("  none")
        return
    cells = [[_cell(r.get(k)) for k, _ in cols] for r in rows]
    widths = [max(len(h), *(len(row[i]) for row in cells)) for i, (_, h) in enumerate(cols)]
    typer.echo(
        ("  " + "  ".join(h.ljust(w) for (_, h), w in zip(cols, widths, strict=True))).rstrip()
    )
    for row in cells:
        typer.echo(
            ("  " + "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True))).rstrip()
        )


def _admin_out(doc: object, as_json: bool, show) -> None:  # type: ignore[no-untyped-def]
    import json

    if as_json:
        typer.echo(json.dumps(doc, default=str, indent=2, sort_keys=True))
    else:
        show(doc)


@admin.command("companies")
def admin_companies(company: _AdminCompany = None, as_json: _AdminJson = False) -> None:
    """Every company: sign-up, owner, setup step, sources, confirmed metrics, last sign-in."""
    from othor.admin import report

    with _admin_session() as s:
        rows = report.companies(s, slug=company)
    _admin_out(
        rows,
        as_json,
        lambda r: _table(
            f"Companies ({len(r)})",
            [
                {
                    **x,
                    "sources": f"{x['sources_connected']}/{x['sources']}",
                    "state": "deleted" if x["deleted"] else "paused" if x["paused"] else "live",
                }
                for x in r
            ],
            [
                ("slug", "Company"),
                ("owner", "Owner"),
                ("signed_up", "Signed up"),
                ("setup_step", "Setup step"),
                ("sources", "Sources"),
                ("metrics_confirmed", "Metrics"),
                ("last_sign_in", "Last sign-in"),
                ("state", "State"),
            ],
        ),
    )


@admin.command("requests")
def admin_requests(as_json: _AdminJson = False) -> None:
    """Connectors owners asked for (M15.4), most requested first: how many asks, how many
    companies, the last one."""
    from othor.admin import report

    with _admin_session() as s:
        rows = report.connector_requests(s)
    _admin_out(
        rows,
        as_json,
        lambda r: _table(
            f"Connector requests ({len(r)})",
            r,
            [
                ("system", "System"),
                ("requests", "Requests"),
                ("companies", "Companies"),
                ("last", "Last"),
            ],
        ),
    )


@admin.command("runs")
def admin_runs(
    company: _AdminCompany = None, days: _AdminDays = 7, as_json: _AdminJson = False
) -> None:
    """Nightly run health: last run per company, failed runs, steps and jobs, the job queue."""
    from othor.admin import report

    with _admin_session() as s:
        doc = report.runs(s, now=_admin_now(), days=days, slug=company)

    def show(d: dict) -> None:  # type: ignore[type-arg]
        _table(
            "Last run per company",
            d["companies"],
            [
                ("slug", "Company"),
                ("last_run", "Run"),
                ("last_kind", "Kind"),
                ("last_run_date", "Date"),
                ("last_status", "Status"),
                ("last_started", "Started"),
                ("last_seconds", "Seconds"),
                ("runs_in_window", f"Runs {days} d"),
                ("failed_in_window", "Failed"),
                ("partial_in_window", "Partial"),
                ("paused", "Paused"),
            ],
        )
        _table(
            f"Failed steps, last {days} days",
            d["failed_steps"],
            [
                ("slug", "Company"),
                ("run", "Run"),
                ("step", "Step"),
                ("at", "At"),
                ("error", "Error"),
            ],
        )
        _table(
            f"Failed jobs, last {days} days",
            d["failed_jobs"],
            [
                ("slug", "Company"),
                ("kind", "Job"),
                ("attempts", "Tries"),
                ("at", "At"),
                ("error", "Error"),
            ],
        )
        _table(
            "Job queue",
            [d["queue"]],
            [
                ("queued", "Queued"),
                ("due_now", "Due now"),
                ("running", "Running"),
                ("oldest_due_minutes", "Oldest due (min)"),
                ("last_job_finished", "Last job finished"),
            ],
        )

    _admin_out(doc, as_json, show)


@admin.command("delivery")
def admin_delivery(
    company: _AdminCompany = None, days: _AdminDays = 7, as_json: _AdminJson = False
) -> None:
    """Brief delivery and email: issues, emails by status, link opens, bounces, failures."""
    from othor.admin import report

    with _admin_session() as s:
        doc = report.delivery(s, now=_admin_now(), days=days, slug=company)

    def show(d: dict) -> None:  # type: ignore[type-arg]
        _table(
            f"Briefs and email, last {days} days",
            d["companies"],
            [
                ("slug", "Company"),
                ("email_delivery", "Email on"),
                ("brief_time", "Brief at"),
                ("issues_ready", "Issues"),
                ("issues_failed", "Issues failed"),
                ("sent", "Sent"),
                ("queued", "Queued"),
                ("failed", "Failed"),
                ("bounced", "Bounced"),
                ("link_opens", "Link opens"),
                ("bounced_recipients", "Bounced recipients"),
            ],
        )
        _table(
            f"Failed and bounced emails, last {days} days",
            d["failures"],
            [
                ("slug", "Company"),
                ("kind", "Kind"),
                ("status", "Status"),
                ("at", "At"),
                ("error", "Error"),
            ],
        )

    _admin_out(doc, as_json, show)


@admin.command("morning")
def admin_morning(company: _AdminCompany = None, as_json: _AdminJson = False) -> None:
    """The morning check (M15.9): last night's run sealed, replay, the ledger, the briefs
    delivered, the backup. Read-only. Exits 1 when any check fails."""
    from pathlib import Path

    from othor.admin import morning
    from othor.core.tenancy import tenant_scope
    from othor.engine.ledger import verify
    from othor.engine.replay import replay

    ctx, _c = _engine_ctx(None)

    def same(run) -> bool:  # type: ignore[no-untyped-def]
        with tenant_scope(run.company_id):
            return bool(replay(ctx, run.id).ok)

    def chain(s, cid) -> tuple[bool, str]:  # type: ignore[no-untyped-def]
        v = verify(s, ctx.settings, cid)
        return (True, f"{v.runs} runs, chain intact") if v.ok else (False, "; ".join(v.problems))

    with ctx.sf() as s:
        rows, instance = morning.report(
            s, Path(ctx.settings.data_dir), _admin_now(), slug=company, replay=same, ledger=chain
        )
    doc = {
        "companies": {slug: [c.__dict__ for c in cs] for slug, cs in rows},
        "instance": [c.__dict__ for c in instance],
    }

    def show(_doc: object) -> None:
        for slug, cs in rows:
            typer.echo(f"\n{slug}")
            for c in cs:
                typer.echo(f"  {c.mark}  {c.name}: {c.detail}")
        typer.echo("\ninstance")
        for c in instance:
            typer.echo(f"  {c.mark}  {c.name}: {c.detail}")

    _admin_out(doc, as_json, show)
    if morning.failed(rows, instance):
        raise typer.Exit(1)


@admin.command("usage")
def admin_usage(
    company: _AdminCompany = None, days: _AdminDays = 30, as_json: _AdminJson = False
) -> None:
    """Cost and usage: model tokens per company and prompt, source queries, storage."""
    from othor.admin import report

    with _admin_session() as s:
        doc = report.usage(s, now=_admin_now(), days=days, settings=get_settings(), slug=company)

    def show(d: dict) -> None:  # type: ignore[type-arg]
        rows = [
            {**x, "storage": None if x["storage_bytes"] is None else _mb(x["storage_bytes"])}
            for x in d["companies"]
        ]
        _table(
            f"Usage, last {days} days",
            rows,
            [
                ("slug", "Company"),
                ("model_calls", "Model calls"),
                ("tokens_in", "Tokens in"),
                ("tokens_out", "Tokens out"),
                ("queries", "Source queries"),
                ("rows_back", "Rows back"),
                ("longest_query_ms", "Longest (ms)"),
                ("storage", "Storage"),
            ],
        )
        _table(
            f"Model tokens by prompt, last {days} days",
            d["prompts"],
            [
                ("slug", "Company"),
                ("prompt", "Prompt"),
                ("calls", "Calls"),
                ("cached", "Cached"),
                ("tokens_in", "Tokens in"),
                ("tokens_out", "Tokens out"),
            ],
        )

    _admin_out(doc, as_json, show)


def _engine_ctx(slug: str | None):  # type: ignore[no-untyped-def]
    import sqlalchemy as sa

    from othor.core.db import make_engine, make_session_factory
    from othor.core.models import Company
    from othor.core.tenancy import system_scope
    from othor.engine.context import EngineContext

    settings = get_settings()
    sf = make_session_factory(make_engine(settings.db_url, pool_pre_ping=True))
    key = InstanceKey.load_or_create(
        settings.instance_key_path, allow_create=settings.env != "prod"
    )
    ctx = EngineContext(settings, sf, key)
    if slug is None:
        return ctx, None
    with sf() as s, system_scope("cli: find the company"):
        c = s.scalars(sa.select(Company).where(Company.slug == slug)).first()
    if c is None:
        typer.echo(f"No company {slug!r}.", err=True)
        raise typer.Exit(1)
    _refuse_closed(c)
    return ctx, c


def _refuse_closed(c) -> None:  # type: ignore[no-untyped-def]
    """A closed company is left alone (M18.2): no runs, compiles or proposals until reopened."""
    if c.deleted_at is None:
        return
    until = f" (until {c.purge_after:%d %b %Y})" if c.purge_after else ""
    typer.echo(f"{c.slug} is closed{until}; `othor admin reopen {c.slug}`.", err=True)
    raise typer.Exit(1)


def _print_run(ctx, company_id, run_id, status) -> None:  # type: ignore[no-untyped-def]
    import sqlalchemy as sa

    from othor.core.tenancy import tenant_scope
    from othor.engine.models import Run

    with ctx.sf() as s, tenant_scope(company_id):
        r = s.scalars(sa.select(Run).where(Run.id == run_id)).one()
        typer.echo(
            f"#{r.number:04d} {r.kind} {r.run_date} {status} · run {r.id}\n"
            f"  inputs {r.input_hash}\n  outputs {r.output_hash}\n  run {r.run_hash}"
        )
        sw = (r.summary or {}).get("sweep")
        if sw:  # computed counts from the run's summary (the Sweep screens are M6b)
            typer.echo(
                f"  sweep {sw['tests']} tests · findings {sw['findings']} · new {sw['new']} · "
                f"needs confirmation {sw['needs_confirmation']} · watching {sw['watching']} · "
                f"resolved {sw['resolved']} · could not run {sw['could_not_run']}"
            )


if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from othor.core.auth import AuthService
    from othor.ontology.models import Spec


@app.command()
def compile(company: str) -> None:
    """Compile a company's latest Ontology version into a spec; print its hash."""
    spec = _compiled(company, None)
    typer.echo(f"v{spec.ontology_version} {spec.hash}")


@app.command("export-spec")
def export_spec(
    company: str, version: Annotated[int | None, typer.Option(help="Ontology version")] = None
) -> None:
    """Print a company's compiled spec (canonical JSON)."""
    from othor.ontology.compiler.api import canonical

    typer.echo(canonical(_compiled(company, version).compiled))


def _compiled(slug: str, version: int | None) -> Spec:
    import sqlalchemy as sa

    from othor.core.db import make_engine, make_session_factory
    from othor.core.models import Company
    from othor.core.tenancy import system_scope, tenant_scope
    from othor.ontology.compiler import compile_version

    sf = make_session_factory(make_engine(get_settings().db_url))
    with sf() as s, system_scope("cli: compile a spec"):
        c = s.scalars(sa.select(Company).where(Company.slug == slug)).first()
    if c is None:
        typer.echo(f"No company {slug!r}.", err=True)
        raise typer.Exit(1)
    _refuse_closed(c)
    with sf() as s, tenant_scope(c.id):
        spec = compile_version(s, c, version, actor="cli")
        s.commit()
        s.expunge(spec)
    return spec


# --- invitations (M11d part C) ---------------------------------------------------------------


def _admin_auth() -> AuthService:
    from othor.core.auth import AuthService
    from othor.core.db import make_engine, make_session_factory

    settings = get_settings()
    sf = make_session_factory(make_engine(settings.db_url, pool_pre_ping=True))
    key = InstanceKey.load_or_create(
        settings.instance_key_path, allow_create=settings.env != "prod"
    )
    return AuthService(sf, key)


@admin.command("reopen")
def admin_reopen(
    slug: Annotated[str, typer.Argument(help="The closed company's slug")],
    yes: Annotated[bool, typer.Option("--yes", help="Don't ask to confirm")] = False,
) -> None:
    """Reopen a company its owner closed, before its purge date (M18.2). Its sources stay
    disconnected until the owner reconnects them."""
    import sqlalchemy as sa

    from othor import closing
    from othor.core.models import Company
    from othor.core.tenancy import system_scope, tenant_scope

    with _admin_session() as s:
        with system_scope("cli: find the company to reopen"):
            c = s.scalars(sa.select(Company).where(Company.slug == slug)).first()
        if c is None:
            typer.echo(f"No company {slug!r}.", err=True)
            raise typer.Exit(1)
        if c.closed_by == "trial":
            typer.echo(
                f"{slug} was closed by its trial's end: use `othor admin trial {slug} --extend N` "
                "or `--end-trial`, which reopen it too.",
                err=True,
            )
            raise typer.Exit(1)
        if not yes and not typer.confirm(f"Reopen {c.name}?"):
            raise typer.Exit(1)
        with tenant_scope(c.id):
            try:
                closing.reopen(s, c, actor="operator", now=_admin_now())
            except closing.CloseError as e:
                typer.echo(str(e), err=True)
                raise typer.Exit(1) from e
            s.commit()
    typer.echo(f"{slug} is open again. The owner signs in, then reconnects each source.")


@admin.command("trial")
def admin_trial(
    slug: Annotated[str, typer.Argument(help="The company's slug")],
    extend: Annotated[int, typer.Option("--extend", help="Days to add to the trial")] = 0,
    end_trial: Annotated[
        bool, typer.Option("--end-trial", help="No trial any more (a paying customer)")
    ] = False,
) -> None:
    """Extend a free trial, or end it for a paying customer (open sign-up, owner 16 Nov 2026).
    A company its trial already closed is reopened too (before its purge date)."""
    import datetime as dt

    import sqlalchemy as sa

    from othor import closing
    from othor.core.events import record_event
    from othor.core.models import Company
    from othor.core.tenancy import system_scope, tenant_scope

    if (extend > 0) == end_trial:
        typer.echo("Give one of --extend N or --end-trial.", err=True)
        raise typer.Exit(2)
    now = _admin_now()
    with _admin_session() as s:
        with system_scope("cli: find the company on a trial"):
            c = s.scalars(sa.select(Company).where(Company.slug == slug)).first()
        if c is None:
            typer.echo(f"No company {slug!r}.", err=True)
            raise typer.Exit(1)
        before = {"trial_ends_at": c.trial_ends_at.isoformat() if c.trial_ends_at else None}
        with tenant_scope(c.id):
            if c.deleted_at is not None:
                if c.closed_by != "trial":
                    typer.echo(
                        f"{slug} was closed by its owner: use `othor admin reopen`.", err=True
                    )
                    raise typer.Exit(1)
                closing.reopen(s, c, actor="operator", now=now)
            if end_trial:
                c.trial_ends_at = None
            else:
                base = max(c.trial_ends_at or now, now)
                c.trial_ends_at = base + dt.timedelta(days=extend)
            c.trial_reminded_at = None
            record_event(
                s,
                actor="operator",
                action="company.trial_changed",
                object_type="company",
                object_id=c.id,
                before=before,
                after={"trial_ends_at": c.trial_ends_at.isoformat() if c.trial_ends_at else None},
                at=now,
            )
            s.commit()
    typer.echo(
        f"{slug}: trial ended, no limit now."
        if end_trial
        else f"{slug}: trial now ends {c.trial_ends_at:%d %b %Y}."
    )


@admin.command("ai-cap")
def admin_ai_cap(
    slug: Annotated[str, typer.Argument(help="The company's slug")],
    tokens: Annotated[str, typer.Argument(help="Tokens a month, or 'default' for the install's")],
) -> None:
    """Set one company's monthly AI token cap (open sign-up, owner 16 Nov 2026)."""
    import sqlalchemy as sa

    from othor.core.events import record_event
    from othor.core.models import Company
    from othor.core.tenancy import system_scope, tenant_scope

    if tokens != "default" and not tokens.isdigit():
        typer.echo("Give a whole number of tokens, or 'default'.", err=True)
        raise typer.Exit(2)
    with _admin_session() as s:
        with system_scope("cli: find the company for its AI cap"):
            c = s.scalars(sa.select(Company).where(Company.slug == slug)).first()
        if c is None:
            typer.echo(f"No company {slug!r}.", err=True)
            raise typer.Exit(1)
        before = c.ai_cap_tokens
        c.ai_cap_tokens = None if tokens == "default" else int(tokens)
        with tenant_scope(c.id):
            record_event(
                s,
                actor="operator",
                action="company.ai_cap_changed",
                object_type="company",
                object_id=c.id,
                before={"ai_cap_tokens": before},
                after={"ai_cap_tokens": c.ai_cap_tokens},
                at=_admin_now(),
            )
            s.commit()
    said = "the install default" if tokens == "default" else f"{tokens} tokens a month"
    typer.echo(f"{slug}: AI cap {said}.")


@admin.command("invite")
def admin_invite(
    email: Annotated[str | None, typer.Option(help="The address the invitation is for")] = None,
    days: Annotated[int, typer.Option(help="Days until it expires")] = 14,
    company_name: Annotated[
        str | None, typer.Option("--company-name", help="Fills in the company name")
    ] = None,
    revoke: Annotated[str | None, typer.Option(help="Revoke this invitation id instead")] = None,
) -> None:
    """Create a sign-up invitation and print its link (shown once), or revoke one."""
    import uuid

    service = _admin_auth()
    if revoke:
        info = service.revoke_invitation(uuid.UUID(revoke))
        if info is None:
            typer.echo("No invitation with that id.", err=True)
            raise typer.Exit(1)
        typer.echo(f"{info.id}  {info.email}  {info.status(_admin_now())}")
        return
    if not email:
        typer.echo("Give --email, or --revoke <id>.", err=True)
        raise typer.Exit(2)
    try:
        info, code = service.create_invitation(email, days=days, company_name=company_name)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    typer.echo(get_settings().instance_url(None, f"/signup?invite={code}"))
    typer.echo(f"for {info.email} · expires {_cell(info.expires_at)} · id {info.id}", err=True)


@admin.command("propose-splits")
def admin_propose_splits(
    company: Annotated[str, typer.Option("--company", help="The company's slug")],
) -> None:
    """Propose on Home the breakdowns a confirmed company's numbers miss (found in its scan).
    Nothing changes until the owner accepts it."""
    import sqlalchemy as sa

    from othor.core.models import Company
    from othor.core.tenancy import tenant_scope
    from othor.setup.discover import propose_splits

    with _admin_session() as s:
        co = s.scalars(sa.select(Company).where(Company.slug == company)).first()
        if co is None:
            typer.echo("No company with that slug.", err=True)
            raise typer.Exit(1)
        _refuse_closed(co)
        with tenant_scope(co.id):
            p = propose_splits(s, co)
            s.commit()
            if p is None:
                typer.echo("Nothing to propose (or proposed before).")
            else:
                typer.echo(f"Proposed on Home: {p.text} · {p.evidence['line']}")


@admin.command("invites")
def admin_invites(as_json: _AdminJson = False) -> None:
    """Every invitation: who, when it expires, and whether it was used or revoked."""
    service = _admin_auth()
    now = _admin_now()
    rows = [
        {
            "id": str(i.id),
            "email": i.email,
            "company_name": i.company_name,
            "created_at": i.created_at,
            "expires_at": i.expires_at,
            "status": i.status(now),
        }
        for i in service.invitations()
    ]
    cols = [
        ("id", "Id"),
        ("email", "Email"),
        ("company_name", "Company"),
        ("created_at", "Created"),
        ("expires_at", "Expires"),
        ("status", "Status"),
    ]
    _admin_out(rows, as_json, lambda r: _table("Invitations", r, cols))


# --- plug-in commands (M20.1) ---------------------------------------------------------------


def _plugin_commands() -> None:
    """Each plug-in's Typer app becomes `othor <name> …`. A settings problem never stops the core
    commands (`othor migrate` must always run)."""
    from othor.core import plugins
    from othor.core.config import get_settings

    try:
        loaded = plugins.loaded() or plugins.load(get_settings())
    except Exception:  # the core CLI works without plug-ins
        return
    for p in loaded:
        for name, sub in p.cli:
            app.add_typer(sub, name=name)


_plugin_commands()


if __name__ == "__main__":
    app()
