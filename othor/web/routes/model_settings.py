# SPDX-License-Identifier: Apache-2.0
"""Settings › AI Model (Any model, 0.37): which model writes Othor AI's words, for all kinds of
work or one kind each; a key pasted in place; Check this model. Everything swaps the pane in
place (htmx); the super admin and admins change it, others see it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response

from othor.ai import choice
from othor.ai.kinds import KINDS, LABELS, NOTES
from othor.ai.providers import routing
from othor.ai.providers.presets import PRESETS
from othor.core.tenancy import tenant_scope
from othor.web.context import services
from othor.web.routes.auth import page
from othor.web.routes.pages import Owner, admin_only, signed_in
from othor.web.security import check_csrf

router = APIRouter(include_in_schema=False)
ALL = routing.ALL
PROVIDER_ORDER = (
    "anthropic",
    "openai",
    "gemini",
    "azure",
    "mistral",
    "deepseek",
    "groq",
    "together",
    "openrouter",
    "ollama",
    "local",
    "template",
)


def _current(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    p = PRESETS.get(row.provider)
    return {
        "provider": row.provider,
        "label": p.label if p else row.provider,
        "model_id": row.model_id,
        "base_url": row.base_url,
        "key_saved": bool(row.key_ciphertext),
        "checked": row.check_result,
    }


def pane_ctx(
    request: Request,
    o: Owner,
    *,
    edit: str | None = None,
    provider: str | None = None,
    form: dict[str, str] | None = None,
    error: tuple[str, str] | None = None,
    check: tuple[str, choice.CheckResult] | None = None,
    split: bool = False,
    saved: str | None = None,
) -> dict[str, Any]:
    """The pane's state: the company's choice for all kinds and per kind, the install's model,
    which row is open for a change, and the last check's result."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        live = routing.rows(s)
        current = {k: _current(live.get(k)) for k in (ALL, *KINDS)}
    install = choice.install_model(svc.settings)
    split = split or any(current[k] for k in KINDS) or (edit in KINDS)
    rows = []
    for k in (ALL, *KINDS) if split else (ALL,):
        cur = current[k]
        row: dict[str, Any] = {
            "kind": k,
            "label": "Every kind of work" if k == ALL else LABELS[k],
            "note": NOTES.get(k, ""),
            "current": cur,
            "same": k != ALL and cur is None,
            "editing": edit == k,
            "check": check[1] if check and check[0] == k else None,
            "saved": saved == k,
        }
        if edit == k:
            base = current[ALL]
            key = (
                provider
                or (cur["provider"] if cur else "")
                or (base["provider"] if k != ALL and base else install["provider"])
            )
            if key not in PRESETS:
                key = "anthropic"
            p = PRESETS[key]
            f = dict(form or {})
            same_provider = bool(cur and cur["provider"] == key)
            row["form"] = {
                "provider": key,
                "preset": p,
                # a new provider starts on its first suggested model: one field fewer to fill
                "model_id": f.get(
                    "model_id",
                    cur["model_id"] if same_provider and cur else (p.models[0] if p.models else ""),
                ),
                "base_url": f.get("base_url", cur["base_url"] if same_provider and cur else ""),
                "key_saved": bool(same_provider and cur and cur["key_saved"]),
                "error": error,
            }
        rows.append(row)
    return {
        "m": {
            "install": install,
            "rows": rows,
            "split": split,
            "can_edit": o.viewer.is_admin,
            "providers": [(k, PRESETS[k].label) for k in PROVIDER_ORDER],
        }
    }


def _pane(request: Request, o: Owner, **kw: Any) -> HTMLResponse:
    return page(request, "partials/settings_model.html", pane_ctx(request, o, **kw))


@router.get("/settings/model", response_class=HTMLResponse)
def model_pane(
    request: Request,
    edit: str = "",
    provider: str = "",
    split: int = 0,
    o: Owner = Depends(signed_in),
) -> Response:
    if edit and not o.viewer.is_admin:
        return _pane(request, o)
    return _pane(
        request,
        o,
        edit=edit if edit in (ALL, *KINDS) else None,
        provider=provider or None,
        split=bool(split),
    )


@router.post("/settings/model", response_class=HTMLResponse, dependencies=[Depends(check_csrf)])
def model_save(
    request: Request,
    kind: str = Form(ALL),
    provider: str = Form(""),
    model_id: str = Form(""),
    base_url: str = Form(""),
    api_key: str = Form(""),
    o: Owner = Depends(admin_only),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        try:
            choice.save(
                s,
                svc.key,
                o.company.id,
                kind=kind,
                provider=provider,
                model_id=model_id,
                base_url=base_url,
                api_key=api_key,
                actor=str(o.viewer.id),
                now=svc.clock(),
            )
        except choice.ChoiceError as e:
            s.rollback()
            return _pane(
                request,
                o,
                edit=kind,
                provider=provider,
                form={"model_id": model_id, "base_url": base_url},
                error=(e.field, e.text),
            )
        s.commit()
    return _pane(request, o, saved=kind, split=kind != ALL)


@router.post(
    "/settings/model/check", response_class=HTMLResponse, dependencies=[Depends(check_csrf)]
)
def model_check(
    request: Request, kind: str = Form(ALL), o: Owner = Depends(admin_only)
) -> Response:
    from othor.ai.gateway import ModelGateway, provider_for

    svc = services(request)
    gw = ModelGateway(
        svc.sf,
        provider_for(svc.settings),
        clock=svc.clock,
        monthly_tokens=svc.settings.ai_monthly_tokens,
    )
    # "every kind": checked as a kind that has no model of its own, so the call goes to it
    run_as = kind
    if kind not in KINDS:
        with svc.sf() as s, tenant_scope(o.company.id):
            own = routing.rows(s)
        run_as = next((k for k in ("reports", *KINDS) if k not in own), "reports")
    result = choice.check(gw, svc.sf, o.company.id, run_as, svc.clock(), actor=str(o.viewer.id))
    return _pane(request, o, check=(kind, result), split=kind != ALL)


@router.post(
    "/settings/model/reset", response_class=HTMLResponse, dependencies=[Depends(check_csrf)]
)
def model_reset(request: Request, kind: str = Form(""), o: Owner = Depends(admin_only)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        choice.reset(
            s, actor=str(o.viewer.id), now=svc.clock(), kind=kind if kind in KINDS else None
        )
        s.commit()
    return _pane(request, o, split=kind in KINDS)
