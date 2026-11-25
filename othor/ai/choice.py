# SPDX-License-Identifier: Apache-2.0
"""Settings › AI Model (Any model, 0.37): the company's own model, for all kinds of work or one
kind; a key stored encrypted; "Check this model".

`save` writes a new choice (the old one soft-deleted) and an event that never carries the key.
`reset` puts the company back on the install's model. `check` runs one small fixed task through
the gateway (same guards, same log, same AI allowance) on made-up figures, never the company's.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from othor.ai.kinds import CHECK_PROMPT, KINDS, LABELS
from othor.ai.models import ModelCall, ModelSetting
from othor.ai.providers import routing
from othor.ai.providers.presets import PRESETS, base_url_of
from othor.core.crypto import InstanceKey
from othor.core.events import record_event

CHECK_VERSION = "v1"
KEY_MAX = 400


class ChoiceError(ValueError):
    """What's wrong with the choice, in plain words (shown under the field)."""

    def __init__(self, field: str, text: str) -> None:
        super().__init__(text)
        self.field = field
        self.text = text


def _public(row: ModelSetting | None) -> dict[str, Any] | None:
    """What a choice may show and log: never the key, only whether one is saved."""
    if row is None:
        return None
    return {
        "kind": row.kind,
        "provider": row.provider,
        "model_id": row.model_id,
        "base_url": row.base_url,
        "key": "saved" if row.key_ciphertext else "none",
    }


def save(
    s: Session,
    key: InstanceKey,
    company_id: uuid.UUID,
    *,
    kind: str,
    provider: str,
    model_id: str = "",
    base_url: str = "",
    api_key: str = "",
    actor: str,
    now: dt.datetime,
) -> ModelSetting:
    """A new choice for `kind` ("all" or one kind). A key left empty keeps the one saved for the
    same provider (in this kind, else in "all"). Inside the company's tenant scope."""
    if kind != routing.ALL and kind not in KINDS:
        raise ChoiceError("kind", "Choose a kind of work.")
    p = PRESETS.get(provider)
    if p is None:
        raise ChoiceError("provider", "Choose a provider.")
    model_id = model_id.strip()[:120]
    url = base_url.strip()[:300] if p.needs_url else ""
    api_key = api_key.strip()
    if len(api_key) > KEY_MAX or any(c.isspace() for c in api_key):
        raise ChoiceError("api_key", "That doesn't look like a key.")
    if p.wire != "none":
        if not model_id:
            raise ChoiceError("model_id", "Enter the model's name.")
        if p.needs_url:
            if not url.startswith(("http://", "https://")):
                raise ChoiceError(
                    "base_url", "Enter the address, starting with http:// or https://"
                )
            if routing.private_url(company_id, base_url_of(p.key, url)):
                raise ChoiceError(
                    "base_url",
                    "This address is on a private network. Othor AI connects only to model "
                    "servers reachable from the internet.",
                )
    live = routing.rows(s)
    before = live.get(kind)
    blob: bytes | None = None
    if p.needs_key or (api_key and p.wire == "openai"):
        if api_key:
            blob = routing.encrypt_key(key, company_id, p.key, api_key)
        else:
            for prev in (live.get(kind), live.get(routing.ALL)):
                if prev is not None and prev.provider == p.key and prev.key_ciphertext:
                    blob = prev.key_ciphertext  # bound to company + provider: still valid
                    break
        if p.needs_key and blob is None:
            raise ChoiceError("api_key", f"Paste your {p.label} key.")
    if before is not None:
        before.deleted_at = now
    row = ModelSetting(
        kind=kind,
        provider=p.key,
        base_url=url,
        model_id=model_id if p.wire != "none" else "",
        key_ciphertext=blob,
        set_by=actor[:64],
    )
    s.add(row)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="model.chosen",
        object_type="model_setting",
        object_id=row.id,
        before=_public(before) or {},
        after=_public(row) or {},
    )
    return row


def reset(s: Session, *, actor: str, now: dt.datetime, kind: str | None = None) -> int:
    """Back to the install's model (kind None: every choice), or one kind back to the company's
    choice for all kinds. Returns how many choices were removed."""
    gone = [r for k, r in routing.rows(s).items() if kind is None or k == kind]
    for r in gone:
        r.deleted_at = now
    if gone:
        record_event(
            s,
            actor=actor,
            action="model.reset",
            object_type="company",
            object_id=gone[0].company_id,
            before={"kinds": sorted(r.kind for r in gone)},
            after={},
        )
    return len(gone)


# --- the view -------------------------------------------------------------------------------


def label_of(provider: str) -> str:
    p = PRESETS.get(provider)
    return p.label if p is not None else provider


def install_model(settings: Any) -> dict[str, str]:
    """The install's own model, in plain words."""
    prov = settings.model_provider
    if prov == "openai_compat":
        prov = settings.model_preset
    if prov == "template":
        return {"provider": "template", "label": "Templates only", "model_id": ""}
    model = settings.model_id or ("claude-opus-5-5" if prov == "anthropic" else "")
    return {"provider": prov, "label": label_of(prov), "model_id": model}


def view(s: Session, settings: Any) -> dict[str, Any]:
    live = routing.rows(s)
    base = live.get(routing.ALL)
    per = {k: live.get(k) for k in KINDS}
    return {
        "install": install_model(settings),
        "all": base,
        "per": per,
        "split": any(per.values()),
        "kinds": [(k, LABELS[k]) for k in KINDS],
    }


# --- Check this model -------------------------------------------------------------------------

# made-up figures, never the company's: the check sends nothing of its data
CHECK_INPUT: dict[str, Any] = {
    "facts": [
        {"slot": "{n1}", "label": "Orders this week"},
        {"slot": "{n2}", "label": "Orders last week"},
        {"slot": "{n3}", "label": "Late deliveries"},
    ],
    "slots": {"{n1}": "Orders this week: 128", "{n2}": "Orders last week: 112", "{n3}": "Late: 4"},
}
CHECK_VALUES = {"{n1}": "128", "{n2}": "112", "{n3}": "4"}


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    title: str
    text: str
    sentence: str = ""
    seconds: float | None = None


_WHY = {
    "unavailable": "Othor AI couldn't use this model. Check the key and the model's name.",
    "template": "This kind of work is set to templates only, so there's no model to check.",
    "error": "The model didn't answer. Check the key, the model's name and the address.",
    "refused": "The model declined to answer.",
    "failed:not-json": "The model answered, but not in the form Othor AI needs.",
    "capped": "This month's AI allowance is used up; the check can run again on the 1st.",
}


def check(
    gateway: Any, sf: Any, company_id: uuid.UUID, kind: str, now: dt.datetime, *, actor: str
) -> CheckResult:
    """One short real call for `kind` through the gateway (fresh, never the cache), scored by
    code: it answered, in JSON, with a sentence that uses the slots and no other figure."""
    from othor.ai.slots import Slots, clean_field

    got = gateway.complete(
        company_id, CHECK_PROMPT, CHECK_VERSION, CHECK_INPUT, kind=kind, fresh=True
    )
    seconds = _latency(sf, company_id)
    if not got.ok or not isinstance(got.output, dict):
        result = CheckResult(False, "It didn't work", _WHY.get(got.guard, _WHY["error"]))
    else:
        slots = Slots(values=dict(CHECK_VALUES), labels=dict(CHECK_INPUT["slots"]))
        said = str(got.output.get("sentence") or "").strip()
        filled = clean_field(said, slots) if said else None
        if not filled or "{n" not in said:
            result = CheckResult(
                False,
                "It answered, but not well enough",
                "It wrote a number itself instead of using the figures it was given. Othor AI "
                "would fall back to its own wording often with this model.",
                seconds=seconds,
            )
        else:
            result = CheckResult(True, "It works", filled, sentence=filled, seconds=seconds)
    _record(sf, company_id, kind, result, now, actor)
    return result


def _latency(sf: Any, company_id: uuid.UUID) -> float | None:
    import sqlalchemy as sa

    from othor.core.tenancy import tenant_scope

    with sf() as s, tenant_scope(company_id):
        row = s.scalars(
            sa.select(ModelCall)
            .where(ModelCall.prompt_id == CHECK_PROMPT)
            .order_by(ModelCall.created_at.desc())
            .limit(1)
        ).first()
        return round(row.latency_ms / 1000, 1) if row is not None and row.latency_ms else None


def _record(
    sf: Any, company_id: uuid.UUID, kind: str, r: CheckResult, now: dt.datetime, actor: str
) -> None:
    from othor.core.tenancy import tenant_scope

    with sf() as s, tenant_scope(company_id):
        row = routing.choice(s, kind)
        if row is not None:
            row.check_result = "ok" if r.ok else "failed"
            row.checked_at = now
        record_event(
            s,
            actor=actor,
            action="model.checked",
            object_type="model_setting",
            object_id=row.id if row is not None else company_id,
            after={"kind": kind, "ok": r.ok, "provider": row.provider if row else ""},
        )
        s.commit()
