# SPDX-License-Identifier: Apache-2.0
"""Plug-ins (M20.1, v0.20): how code outside the core extends Othor AI.

The core never names a plug-in. It loads every installed package in the `othor.plugins`
entry-point group, plus any `pkg.mod:factory` listed in OTHOR_PLUGINS (tests, self-host
customisation); OTHOR_PLUGINS=none turns them all off. Each factory takes the Settings and
returns a `Plugin`, every part optional:

- `routers`, `static`, `templates`: pages of its own (included before the core's catch-alls);
  a static entry is mounted at `/static/p/<name>/`, or at `<name>` itself when it starts with
  "/" (the website keeps its URLs, M20.2);
- `root_page`, `indexable`: a website's landing on the root host and its indexable pages;
- `nav`: side-menu items, in the company group above Setup unless they configure the install;
- `fragments`: content for named slots inside the open features ("settings.company.advanced",
  "sources.add.kinds", …), so an enterprise feature can extend an open one;
- `connectors`: data-source kinds; `jobs`, `cli`, `prompts`, `models`, `migrations`;
- `on_load(settings)`: anything else at start: importing its detectors so they `register()`,
  `othor.ontology.types.register_body()` for its record types (the core layer can't import
  them itself), and the registries of Advanced Intelligence Phase 0 (17 Nov 2026):
  `othor.engine.proposals.register_proposal` (a Home proposal kind),
  `othor.engine.orchestrator.register_run_input` (a stored run input that replay reads, never
  fetches), `othor.engine.detect.register_pool` with `Spec.pool` (its own Benjamini–Hochberg
  pool), `othor.ontology.compiler.build.register_compiler` (its records' part of the spec) and
  `othor.briefs.sections.register_brief_section` (a brief section beside the ranked four);
- `after_scan(session, company_id, source_id)`: work after a source's scan (next to the
  core's split offers); `after_seal(worker_ctx, company_id, run_id)`: work after a run is sealed
  done or partial (no model needed);
  `ticks(worker_ctx)`: functions on the worker's minute loop, each idempotent. Each is guarded:
  a failing hook is logged and never fails the scan, run or loop;
- `hosts`: extra outbound hosts its fetches may reach (`othor.core.http`), listed on System
  Health.

A plug-in that fails to load is logged and skipped: the app always starts. A plug-in may not
replace a core job kind, connector kind or path. Plug-ins run with the app's full trust (only
installed packages and the operator's own OTHOR_PLUGINS load; docs/security.md).
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

log = logging.getLogger("othor.plugins")

GROUP = "othor.plugins"
# job kinds the core owns (othor/jobs/runner.py); a plug-in can't replace one
CORE_JOBS = frozenset(
    {
        "scan",
        "read",
        "label",
        "upload",
        "draft",
        "business",
        "chat",
        "run",
        "deliver",
        "alert",
        "export",
        "replay",
    }
)
CORE_CONNECTORS = frozenset({"postgres", "mysql", "gsheets"})


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    href: str
    icon: str
    group: str = "company"  # "company" (above Setup) or "setup"
    after: str | None = None  # the core item it follows; None = the group's end
    tag: str | None = None  # "ee": the Enterprise Edition tag beside the label (17 Nov 2026)
    # a count beside the label (new things to look at); (session, company) -> int, 0 = none
    badge: Callable[[Any, Any], int] | None = None


@dataclass(frozen=True)
class Fragment:
    slot: str
    template: str
    context: Callable[..., dict[str, Any] | None] | None = None
    # a settings or sources form field this fragment saves: field -> handler(session, company,
    # value, actor) returning (before, after) for the event row
    fields: dict[str, Callable[..., tuple[Any, Any]]] = field(default_factory=dict)


@dataclass(frozen=True)
class Plugin:
    name: str
    routers: tuple[Any, ...] = ()
    static: tuple[tuple[str, Path], ...] = ()
    templates: tuple[Path, ...] = ()
    # folders holding its components*.css: their classes are components to el() (M20.2)
    styles: tuple[Path, ...] = ()
    nav: tuple[NavItem, ...] = ()
    fragments: tuple[Fragment, ...] = ()
    connectors: tuple[Any, ...] = ()
    jobs: dict[str, Callable[..., Any]] = field(default_factory=dict)
    cli: tuple[tuple[str, Any], ...] = ()
    prompts: tuple[Path, ...] = ()
    models: tuple[str, ...] = ()
    migrations: Path | None = None
    on_load: Callable[[Any], None] | None = None
    # M20.2: the page "/" shows on the root host when the public site is on (a website's
    # landing), and the paths search engines may index (everything else is noindex)
    root_page: Callable[[Any], Any] | None = None
    indexable: Callable[[], list[str]] | None = None
    # Advanced Intelligence Phase 0 (17 Nov 2026): lifecycle hooks and outbound hosts
    after_scan: tuple[Callable[[Any, Any, Any], None], ...] = ()
    after_seal: tuple[Callable[[Any, Any, Any], None], ...] = ()
    ticks: tuple[Callable[[Any], None], ...] = ()
    hosts: frozenset[str] = frozenset()


_loaded: tuple[Plugin, ...] | None = None
_settings: Any = None


def reset() -> None:
    """Forget what was loaded (tests)."""
    global _loaded, _settings
    _loaded = None
    _settings = None


def settings() -> Any:
    """The settings the plug-ins were loaded with (the app's), or None before `load`."""
    return _settings


def _factories(spec: str) -> list[tuple[str, Callable[[Any], Plugin] | None]]:
    out: list[tuple[str, Callable[[Any], Plugin] | None]] = []
    if spec.strip().lower() == "none":
        return out
    for ep in sorted(entry_points(group=GROUP), key=lambda e: e.name):
        try:
            out.append((f"{ep.name} ({ep.value})", ep.load()))
        except Exception:
            log.exception("plug-in %s couldn't be imported; skipped", ep.value)
    for ref in (x.strip() for x in spec.split(",")):
        if not ref:
            continue
        mod, _, attr = ref.partition(":")
        try:
            out.append((ref, getattr(importlib.import_module(mod), attr or "plugin")))
        except Exception:
            log.exception("plug-in %s couldn't be imported; skipped", ref)
            out.append((ref, None))
    return out


def _problem(p: Plugin, taken_jobs: set[str], taken_kinds: set[str]) -> str | None:
    clash = sorted(set(p.jobs) & (CORE_JOBS | taken_jobs))
    if clash:
        return f"job kind {', '.join(clash)} is already taken"
    kinds = {getattr(c, "kind", "") for c in p.connectors}
    clash = sorted(kinds & (CORE_CONNECTORS | taken_kinds))
    if clash:
        return f"connector kind {', '.join(clash)} is already taken"
    return None


def load(settings: Any) -> tuple[Plugin, ...]:
    """Every plug-in, once per process (the first call's settings decide)."""
    global _loaded, _settings
    if _loaded is not None:
        return _loaded
    _settings = settings
    got: list[Plugin] = []
    jobs: set[str] = set()
    kinds: set[str] = set()
    for ref, factory in _factories(str(getattr(settings, "plugins", "") or "")):
        if factory is None:
            continue
        try:
            p = factory(settings)
            problem = _problem(p, jobs, kinds)
            if problem:
                log.error("plug-in %s refused: %s", ref, problem)
                continue
            if p.on_load is not None:
                p.on_load(settings)
        except Exception:
            log.exception("plug-in %s failed to load; skipped", ref)
            continue
        jobs |= set(p.jobs)
        kinds |= {getattr(c, "kind", "") for c in p.connectors}
        got.append(p)
        log.info("plug-in %s loaded", p.name)
    _loaded = tuple(got)
    return _loaded


def loaded() -> tuple[Plugin, ...]:
    """What `load` found (empty before it ran)."""
    return _loaded or ()


# --- the parts, gathered across plug-ins ---------------------------------------------------------


def jobs() -> dict[str, Callable[..., Any]]:
    return {k: v for p in loaded() for k, v in p.jobs.items()}


def nav(group: str) -> tuple[NavItem, ...]:
    return tuple(n for p in loaded() for n in p.nav if n.group == group)


def fragments(slot: str) -> tuple[Fragment, ...]:
    return tuple(f for p in loaded() for f in p.fragments if f.slot == slot)


def connectors() -> tuple[Any, ...]:
    return tuple(c for p in loaded() for c in p.connectors)


def root_page() -> Callable[[Any], Any] | None:
    return next((p.root_page for p in loaded() if p.root_page is not None), None)


def indexable_paths() -> list[str]:
    return [path for p in loaded() if p.indexable is not None for path in p.indexable()]


def hosts() -> frozenset[str]:
    return frozenset(h for p in loaded() for h in p.hosts)


def guarded(what: str, fn: Callable[..., Any], *args: Any) -> bool:
    """Call one plug-in hook; a failure is logged, never raised (the core's work goes on)."""
    try:
        fn(*args)
    except Exception:
        log.exception("plug-in %s hook %s failed", what, getattr(fn, "__qualname__", fn))
        return False
    return True


def after_scan(session: Any, company_id: Any, source_id: Any) -> None:
    """Each plug-in's after-scan work, each in its own savepoint: one that fails is rolled back
    and logged, and the scan finishes as it would have without it."""
    for p in loaded():
        for fn in p.after_scan:
            nested = session.begin_nested()
            if guarded("after_scan", fn, session, company_id, source_id):
                nested.commit()
            else:
                nested.rollback()


def after_seal(ctx: Any, company_id: Any, run_id: Any) -> None:
    for p in loaded():
        for fn in p.after_seal:
            guarded("after_seal", fn, ctx, company_id, run_id)


def tick(ctx: Any) -> None:
    for p in loaded():
        for fn in p.ticks:
            guarded("tick", fn, ctx)


def field_handler(name: str) -> Callable[..., tuple[Any, Any]] | None:
    for p in loaded():
        for f in p.fragments:
            if name in f.fields:
                return f.fields[name]
    return None
