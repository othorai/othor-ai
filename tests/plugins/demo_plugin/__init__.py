# SPDX-License-Identifier: Apache-2.0
"""A plug-in that uses every hook (M20.1), for the tests only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from othor.connectors.kinds import ConnectorKind
from othor.core.plugins import Fragment, NavItem, Plugin

HERE = Path(__file__).resolve().parent

router = APIRouter()


@router.get("/demo", response_class=HTMLResponse)
def demo_page() -> HTMLResponse:
    from othor.web.templating import templates

    return HTMLResponse(templates.env.get_template("demo/page.html").render())


cli = typer.Typer(help="The demo plug-in")


@cli.command()
def hello() -> None:
    typer.echo("hello from the demo plug-in")


def _job(ctx: Any, job: Any) -> None:
    from othor.core.events import SYSTEM_ACTOR, record_event
    from othor.core.tenancy import tenant_scope

    with ctx.sf() as s, tenant_scope(job.company_id):
        record_event(s, actor=SYSTEM_ACTOR, action="demo.ran", object_type="job", object_id=job.id)
        s.commit()


def _level(s: Any, company: Any, value: str, actor: str) -> tuple[Any, Any]:
    if value not in ("basic", "advanced"):
        raise ValueError("Pick basic or advanced.")
    return None, value


def _on_load(settings: Any) -> None:
    from pydantic import BaseModel

    from othor.ontology.types import BODIES, register_body

    class DemoBody(BaseModel):
        text: str

    if "demo_note" not in BODIES:
        register_body("demo_note", DemoBody)  # type: ignore[arg-type]


def plugin(settings: Any) -> Plugin:
    return Plugin(
        name="demo",
        routers=(router,),
        static=(("demo", HERE / "static"),),
        templates=(HERE / "templates",),
        nav=(NavItem("demo", "Demo", "/demo", "dna", group="company", after="summary"),),
        fragments=(
            Fragment(
                "settings.company.advanced",
                "demo/advanced.html",
                fields={"demo_level": _level},
            ),
            Fragment("home.end", "demo/home_end.html", context=lambda ctx: {"greeting": "Hi"}),
            Fragment("sources.add.kinds", "demo/add_kind.html"),
        ),
        connectors=(ConnectorKind("demo_feed", "Demo feed", "DF", "local", "duckdb"),),
        jobs={"demo_job": _job},
        cli=(("demo", cli),),
        prompts=(HERE / "prompts",),
        models=("tests.plugins.demo_plugin.models",),
        migrations=HERE / "migrations",
        on_load=_on_load,
    )


def clashing(settings: Any) -> Plugin:
    return Plugin(name="clash", jobs={"run": _job})


def with_detector(settings: Any) -> Plugin:
    """A plug-in whose on_load registers a detector (the unit test removes it again)."""

    def _register(_settings: Any) -> None:
        from othor.engine.detectors.base import Spec, register

        register(Spec("demo_detector", "1", "Demo", run=lambda ctx: ([], None)))  # type: ignore[arg-type]

    return Plugin(name="demo_detector", on_load=_register)
