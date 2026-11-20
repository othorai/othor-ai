# SPDX-License-Identifier: Apache-2.0
"""What Othor AI drafts for briefs (docs/06): a new brief from a question (4c1), a one-line change
in Edit brief (4d2), and up to 3 suggested briefs (4b). Drafts only: every key and value is
checked against the spec (`edit.clean`), numbers are never taken from a model (the reasons on a
suggestion must be grounded in the input), and nothing changes until the owner applies it.
Outside the nightly run; the run never imports this module.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.gateway import ModelGateway
from othor.ai.guards import numbers_grounded
from othor.briefs import edit
from othor.briefs.models import SuggestedBrief

# v2 (12 Nov 2026): exclude/include as lists of {breakdown, values}; v1's free-form maps were
# rejected by the provider's structured output (every draft fell back to "isn't available")
# v4 (20 Nov 2026): only the numbers that answer the question (no padding to 6), what it asks
# about that no number measures (`missing`), and the targets the owner states (`targets`)
DRAFT, EDIT, SUGGEST = ("brief_draft", "v4"), ("brief_edit", "v3"), ("brief_suggest", "v1")
MISSING_MAX, TARGETS_MAX, PHRASE_MAX = 3, 3, 80


def _by_breakdown(v: Any) -> dict[str, list[str]]:
    """The model's {breakdown, values} list (v2) or {breakdown: values} map (v1) as a map."""
    if isinstance(v, dict):
        return {str(b): [str(x) for x in vals or []] for b, vals in v.items()}
    out: dict[str, list[str]] = {}
    for item in v or []:
        if isinstance(item, dict) and item.get("breakdown"):
            out.setdefault(str(item["breakdown"]), []).extend(
                str(x) for x in item.get("values") or []
            )
    return out


SUGGEST_MAX = 3
UNAVAILABLE = "Othor AI isn’t available right now; change it with the fields below."

# A draft's name from its question (v0.11): the words that carry it, without the asking words
_ASKING = {
    "which", "what", "who", "whom", "whose", "how", "where", "when", "why", "are", "is", "am",
    "was", "were", "do", "does", "did", "can", "could", "should", "will", "would", "has",
    "have", "had", "tell", "show", "give", "let", "know",
}  # fmt: skip
_FILLER = {
    "my", "our", "the", "a", "an", "we", "i", "me", "us", "still", "it", "this", "that",
    "these", "those", "you", "your", "please", "really", "are", "is", "do", "does", "did",
    "was", "were", "there", "be",
}  # fmt: skip
_TRAILING = {"and", "or", "on", "of", "for", "to", "in", "by", "with", "at", "from", "vs"}
NAME_WORDS, NAME_MAX = 4, 60


def name_from(question: str, taken: Iterable[str] = ()) -> str:
    """A draft's name from its question, never another brief's ("Which export customers are
    paying late?" → "Export customers paying late"): the first words that carry it, without the
    asking words; "New brief" when none are left; a number after it when a brief has the name."""
    words = re.findall(r"[^\s?!.,;:()\"“”]+", question)
    while words and words[0].lower() in _ASKING | _FILLER:
        words.pop(0)
    words = [w for w in words if w.lower() not in _FILLER][:NAME_WORDS]
    while words and words[-1].lower() in _TRAILING:
        words.pop()
    name = " ".join(words)[:NAME_MAX].strip()
    name = name[:1].upper() + name[1:] if name else "New brief"
    return unique_name(name, taken)


def unique_name(name: str, taken: Iterable[str]) -> str:
    """The name, or "<name> 2", "<name> 3" … when an existing brief already has it."""
    have = {t.strip().lower() for t in taken}
    if name.lower() not in have:
        return name
    n = 2
    while f"{name} {n}".lower() in have:
        n += 1
    return f"{name} {n}"


def _lists(spec: dict[str, Any], options: dict[str, list[str]]) -> dict[str, Any]:
    metrics = spec.get("metrics", {})
    return {
        "metrics": [
            {"key": k, "name": m.get("name"), "family": m.get("family"), "unit": m.get("unit")}
            for k, m in sorted(metrics.items())
            if m.get("status") == "active"
        ],
        "breakdowns": options,
        "owners": [
            {"name": o["name"], "areas": o.get("areas", [])} for o in spec.get("owners") or []
        ],
        "goals": [
            {"key": g["key"], "name": g.get("name", "")}
            for g in spec.get("forecast", {}).get("goals", [])
        ],
    }


def draft_brief(
    gateway: ModelGateway,
    company_id: uuid.UUID,
    question: str,
    spec: dict[str, Any],
    options: dict[str, list[str]],
    fallback: dict[str, Any],
    taken: Iterable[str] = (),
    name: str = "",
) -> tuple[dict[str, Any], str]:
    """(draft, how): the model's draft checked against the spec, or the fallback (the default
    brief's numbers with this question) when the model is off or its draft doesn't hold. The
    draft also carries `missing` and `targets` (v4), for the panel only: `edit.clean` leaves them
    out of the brief's version.
    The name is the model's, else `name` (a suggested brief's), else made from the question:
    never the fallback's, never an existing brief's (`taken`)."""
    taken = [str(t) for t in taken]
    have = {t.strip().lower() for t in taken}
    inputs = {"question": question, **_lists(spec, options)}
    got = gateway.complete(company_id, *DRAFT, inputs)
    own = name.strip() if name.strip().lower() not in have else ""
    base = {**fallback, "question": question, "name": own or name_from(question, taken)}
    if not got.ok or not isinstance(got.output, dict):
        # by rules: the default brief's numbers with this question, nothing noted
        return {**edit.clean(base, spec, options), "missing": [], "targets": []}, got.guard
    o = got.output
    said = str(o.get("name") or "").strip()
    body = {
        **base,
        "name": said if said and said.lower() not in have else base["name"],
        "question": o.get("question")
        if numbers_grounded(o.get("question", ""), inputs)
        else question,
        "headline": o.get("headline") or base.get("headline"),
        "scope": {"metrics": None, "exclude": _by_breakdown(o.get("exclude"))},
        "ask_owner": o.get("ask_owner") or "top_item",
        "goals": o.get("goals") or [],
        "schedule": o.get("schedule") or "daily",
        "on": _on(o, question),
    }
    cleaned = edit.clean(body, spec, options)
    if not cleaned["headline"]:
        cleaned["headline"] = edit.clean(base, spec, options)["headline"]
    return {
        **cleaned,
        "missing": missing(o.get("missing")),
        "targets": targets(o.get("targets"), question),
    }, "drafted"


def _phrases(v: Any, n: int) -> list[str]:
    out: list[str] = []
    for x in v if isinstance(v, list) else []:
        t = " ".join(str(x).split()).strip(" .·")[:PHRASE_MAX]
        if t and t.lower() not in {o.lower() for o in out}:
            out.append(t)
    return out[:n]


def missing(v: Any) -> list[str]:
    """What the question asks about that no number measures ("dispatch time"), as the model said
    it: up to 3 short phrases, and none with a digit in it (no number from a model)."""
    return _phrases([x for x in v or [] if not any(c.isdigit() for c in str(x))], MISSING_MAX)


def targets(v: Any, question: str) -> list[str]:
    """The targets the owner stated ("DSO under 45 days"), in their words: kept only when the
    phrase has a figure and every figure in it is one the owner typed in the question (CLAUDE.md
    1). Each becomes a goal only through Add a goal, when the owner clicks it."""
    said = set(re.findall(r"\d+(?:\.\d+)?", question.replace(",", "")))
    out = []
    for t in _phrases(v, TARGETS_MAX * 2):
        digits = re.findall(r"\d+(?:\.\d+)?", t.replace(",", ""))
        if digits and all(d in said for d in digits):
            out.append(t)
    return out[:TARGETS_MAX]


def _on(o: dict[str, Any], said: str) -> str | None:
    """The day a Weekly or Monthly brief comes out, from the model's draft: a day of the month
    only when the owner typed that number (no number from a model, CLAUDE.md 1); `edit.clean`
    then keeps it to a real day."""
    on = o.get("on")
    if on is None:
        return None
    on = str(on).strip().lower()
    if any(c.isdigit() for c in on) and not numbers_grounded(on, {"said": said}):
        return None
    return on


def change_line(
    gateway: ModelGateway,
    company_id: uuid.UUID,
    line: str,
    draft: dict[str, Any],
    spec: dict[str, Any],
    options: dict[str, list[str]],
) -> tuple[dict[str, Any], str]:
    """One line ("leave out Export") into the draft: (new draft, reply). The model's reply is
    shown only when it holds no number the input doesn't have."""
    inputs = {"line": line, "brief": draft, **_lists(spec, options)}
    got = gateway.complete(company_id, *EDIT, inputs)
    if not got.ok or not isinstance(got.output, dict):
        return draft, UNAVAILABLE
    o = got.output
    d = {**draft, "scope": {**(draft.get("scope") or {})}}
    for k in ("name", "question", "ask_owner", "schedule", "email_format"):
        if o.get(k):
            d[k] = o[k]
    if o.get("on"):
        d["on"] = _on(o, line)
    head = [m for m in d.get("headline") or [] if m not in (o.get("remove_headline") or [])]
    head += [m for m in o.get("add_headline") or [] if m not in head]
    d["headline"] = head
    ex = {b: list(v) for b, v in (d["scope"].get("exclude") or {}).items()}
    for b, vals in _by_breakdown(o.get("exclude")).items():
        ex[b] = sorted({*ex.get(b, []), *vals})
    for b, vals in _by_breakdown(o.get("include")).items():
        ex[b] = [v for v in ex.get(b, []) if v not in vals]
    d["scope"]["exclude"] = ex
    reply = str(o.get("reply") or "")
    if not numbers_grounded(reply, inputs):
        reply = "Changed. Check the fields below."
    return edit.clean(d, spec, options), reply


def suggest(
    s: Session,
    gateway: ModelGateway,
    company_id: uuid.UUID,
    spec: dict[str, Any],
    message: str,
    findings: list[str],
    existing: list[str],
) -> list[SuggestedBrief]:
    """Up to 3 suggested briefs (4b), stored as `shown`; a reason with a number the input
    doesn't have is dropped. Not again while 3 are still shown."""
    shown = s.scalars(sa.select(SuggestedBrief).where(SuggestedBrief.status == "shown")).all()
    if len(shown) >= SUGGEST_MAX:
        return list(shown)
    inputs = {
        "message": message,
        **_lists(spec, {}),
        "findings": findings,
        "existing": existing,
    }
    got = gateway.complete(company_id, *SUGGEST, inputs)
    if not got.ok or not isinstance(got.output, dict):
        return list(shown)
    have = {x.question.strip().lower() for x in shown} | {q.strip().lower() for q in existing}
    metrics = {k for k, m in spec.get("metrics", {}).items() if m.get("status") == "active"}
    out = list(shown)
    for sug in got.output.get("suggestions") or []:
        if len(out) >= SUGGEST_MAX:
            break
        q = str(sug.get("question") or "").strip()
        if not q or q.lower() in have or not numbers_grounded(q, inputs):
            continue
        why = [w for w in sug.get("why") or [] if numbers_grounded(str(w), inputs)][:3]
        head = [m for m in sug.get("headline") or [] if m in metrics][: edit.HEADLINE_MAX]
        row = SuggestedBrief(
            question=q[:300],
            name=str(sug.get("name") or "Suggested brief")[:120],
            draft={
                "headline": head,
                "area": str(sug.get("area") or ""),
                "from": sug.get("from") or [],
            },
            evidence=why,
            prompt_version="v1",
        )
        s.add(row)
        out.append(row)
        have.add(q.lower())
    return out
