# SPDX-License-Identifier: Apache-2.0
"""What the setup screens show (2b, 2c, 2d, 2m, 2n, 2na): rows built from stored drafts and
computed values. Every figure on screen is formatted here from a computed number (fmt)."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.gateway import model_status
from othor.core import fmt
from othor.core.config import Settings
from othor.jobs.models import Job
from othor.ontology import service
from othor.setup import confirm, drafts, uploads
from othor.setup.models import SetupDraft, Upload

GRAIN = {"day": "daily", "week": "weekly", "month": "monthly"}
CONF = {
    "conflict": ("Conflict", "neg"),
    "check": ("Check", "warn"),
    "low": ("Low", "warn"),
    "high": ("High", "ok"),
}
NO_MODEL = "Drafts unavailable · suggested by rules. You can add or edit metrics yourself."
NOT_AVAILABLE = "Not available right now"
ORDER = {"conflict": 0, "check": 1, "low": 2, "high": 3}


# --- values ---------------------------------------------------------------------------------


def value_text(v: Decimal | str | None, unit: str) -> str:
    if v is None:
        return "—"
    d = Decimal(str(v))
    if unit == "inr":
        return fmt.inr(d)
    if unit == "metres":
        return fmt.num(d) + " m"
    if unit == "days":
        return fmt.days(d)
    if unit == "percent":
        return fmt.pct(d, decimals=1)
    if unit in ("ratio", "inr_per_metre"):
        return fmt.inr_plain(d, decimals=2) if unit == "inr_per_metre" else fmt.num(d, decimals=2)
    return fmt.num(d)


def period_text(value: dict[str, Any]) -> str:
    p = value.get("period") or {}
    if not p:
        return ""
    start = dt.date.fromisoformat(p["start"])
    end = dt.date.fromisoformat(p["end"]) - dt.timedelta(days=1)
    if p.get("grain") == "month":
        return f"{start:%b} {start.year}"
    if p.get("grain") == "week":
        return f"Week of {fmt.date_short(start)}"
    return fmt.date_short(end)


def value_line(r: SetupDraft) -> str | None:
    v = r.value or {}
    unit = r.body.get("unit", "inr")
    if v.get("state") == "ok":
        return f"{period_text(v)} · {value_text(v.get('value'), unit)}"
    if v.get("state") == "no_data":
        return f"{period_text(v)} · no rows"
    if v.get("state") == "not_read":
        return "not read yet · values appear after the sheet is read"
    if v.get("state") == "not_determinable":
        return f"{period_text(v)} · not determinable"
    return None


# --- metric rows ------------------------------------------------------------------------------


def _where(r: SetupDraft) -> list[str]:
    out = []
    for x in r.refs or []:
        loc = str(x.get("locator") or "")
        m = re.match(r"tab (.+?)!", loc)
        if m:
            loc = f"tab “{m.group(1)}”"
        line = " · ".join(p for p in (x.get("upload"), loc) if p)
        if line not in out:
            out.append(line)
    if not out:
        t = r.body.get("table") or {}
        if t:
            out.append(t.get("source", ""))
    return out


WHERE_NOTE = re.compile(
    r"could not find|couldn.t find|not in |is it in|which (table|column)|missing", re.I
)


def connect_url(key: str, kind: str, sid: Any = None) -> str:
    return f"/setup/metrics?panel=connect&key={key}&kind={kind}" + (f"&sid={sid}" if sid else "")


def chat_url(key: str, purpose: str) -> str:
    return f"/setup/metrics?panel=chat&purpose={purpose}&key={key}"


CHAT_TITLES = {
    "where": "Where is it?",
    "connect": "Connect a source",
    "edit": "Change the definition",
    "fix": "Fix with Othor AI",
}


def _intro(row: SetupDraft, purpose: str) -> str:
    """The panel's first line, written by code from the draft (no model, no invented number)."""
    name = row.body.get("name") or row.key
    if purpose == "where":
        if row.state == "needs_source" and (row.origin or {}).get("reason") and row.note:
            return (
                f"{name}: {row.note.rstrip(' .')}. Say where it is or how it's calculated, "
                "or pick from what was found in your sources:"
            )
        if row.state == "needs_source" or not row.note:
            return f"{name} was drafted without a column. Candidates found in your sources:"
        return f"{name}: {row.note.rstrip(' .')} Candidates found in your sources:"
    if purpose == "connect":
        return f"{name} was drafted without a source. Connect one, or use a table you already have:"
    what = (row.body.get("description") or "its current definition").rstrip(" .")
    return f"{name}: {what}. What would you like to change?"


def _card(proposal: dict[str, Any], unit: str) -> dict[str, Any]:
    """The draft card: today's value in the patched definition's unit, the old one in its own."""
    value = proposal.get("value") or {}
    before = proposal.get("before") or {}
    new_unit = (proposal.get("card") or {}).get("body", {}).get("unit") or unit
    old_unit = proposal.get("before_unit") or unit
    return {
        "lines": proposal.get("card", {}).get("lines", []),
        "now": value_line_of(value, new_unit),
        "before": value_line_of(before, old_unit) if before.get("state") == "ok" else None,
    }


def value_line_of(value: dict[str, Any], unit: str) -> str | None:
    if value.get("state") == "ok":
        return f"{period_text(value)} · {value_text(value.get('value'), unit)}"
    if value.get("state") == "no_data":
        return f"{period_text(value)} · no rows"
    if value.get("state") == "not_read":
        return "not read yet · values appear after the sheet is read"
    if value.get("state") == "error":
        return "couldn’t compute today’s value"
    return None


def chat_ctx(
    s: Session,
    row: SetupDraft,
    purpose: str,
    replies: list[Any],
    model: bool,
    page: str | None = None,
    close: str = "/setup/metrics",
) -> dict[str, Any]:
    """`page`/`close`: where this chat lives (Metrics, or the Sources review step for 6ha)."""
    from othor.setup import chat

    thread = chat.thread_for(s, row.key, purpose, create=False)
    msgs = []
    pending = False
    for m in chat.messages(s, thread):
        p = m.proposal or {}
        pending = pending or (m.role == "user" and p.get("state") == "pending")
        msgs.append(
            {
                "id": m.id,
                "role": m.role,
                "text": m.text,
                "applied": m.applied,
                "card": _card(p, row.body.get("unit", "inr")) if p.get("card") else None,
            }
        )
    label, dot = CONF.get(row.confidence, CONF["check"])
    return {
        "kind": "chat",
        "page": page or chat_url(row.key, purpose),
        "close": close,
        "elsewhere": page is not None,
        "purpose": purpose,
        "key": row.key,
        "title": CHAT_TITLES.get(purpose, "Fix"),
        "name": row.body.get("name") or row.key,
        "label": label,
        "dot": dot,
        "intro": _intro(row, purpose),
        "today": value_line(row),
        "replies": [
            {"i": i, **q.as_json()}
            for i, q in enumerate(replies)
            if not (page and q.action == "form_panel")  # the setup form lives on Metrics
        ],
        "messages": msgs,
        "pending": pending,
        "model": model,
        "not_available": NOT_AVAILABLE,
    }


def metric_row(r: SetupDraft, sug: Any = None) -> dict[str, Any]:
    b = r.body
    breakdowns = ", ".join(x.replace("_", " ") for x in b.get("breakdowns", []))
    sub = " · ".join(p for p in (GRAIN.get(b.get("grain", "month"), ""), breakdowns) if p)
    conf = r.confidence
    if r.state in ("needs_source", "excluded_table"):
        conf = "check"
    label, dot = CONF.get(conf, CONF["check"])
    if r.state == "confirmed" and not r.changed:  # the owner settled it: confidence is high
        label, dot = CONF["high"]
    origin = r.origin or {}
    rule = origin.get("rule") if origin.get("by") == "rules" else None
    actions: list[tuple[str, str, str]] = []  # (label, kind, url)
    if r.changed:
        actions = [
            ("Accept", "primary", f"/setup/metrics/{r.key}/accept"),
            ("Keep mine", "", f"/setup/metrics/{r.key}/reject"),
        ]
    elif conf == "conflict":
        actions = [("Choose", "primary", f"/setup/metrics?panel=choose&key={r.key}")]
    elif r.state == "excluded_table":
        actions = [
            ("Use without personal columns", "", f"/setup/metrics/{r.key}/without-personal"),
            ("Skip", "quiet", f"/setup/metrics/{r.key}/skip"),
        ]
    elif r.state == "needs_source" and sug is not None:  # 2n: connect what it names
        from othor.setup.connect import connect_label

        actions = [
            (connect_label(sug), "", connect_url(r.key, sug.kind)),
            ("Skip", "quiet", f"/setup/metrics/{r.key}/skip"),
        ]
    elif r.state == "needs_source":
        actions = [
            ("Where is it?", "", chat_url(r.key, "where")),
            ("Connect a source", "", chat_url(r.key, "connect")),
            ("Skip", "quiet", f"/setup/metrics/{r.key}/skip"),
        ]
    elif r.state == "confirmed":
        actions = [("Edit", "quiet", chat_url(r.key, "edit"))]
    elif r.note and WHERE_NOTE.search(r.note):
        actions = [
            ("Where is it?", "", chat_url(r.key, "where")),
            ("Confirm", "quiet", f"/setup/metrics/{r.key}/confirm"),
        ]
    else:
        actions = [
            ("Confirm", "", f"/setup/metrics/{r.key}/confirm"),
            ("Edit", "quiet", chat_url(r.key, "edit")),
        ]
    doc = origin.get("document") or {}
    return {
        "key": r.key,
        "name": b.get("name") or r.key,
        "sub": sub,
        "definition": b.get("description") or "",
        "note": r.note or doc.get("note"),
        "doc_changed": bool(doc) and doc.get("round") == r.draft_no,
        "value": value_line(r),
        "where": _where(r),
        "rule": rule,
        "label": label,
        "dot": dot,
        "conflict": conf == "conflict",
        "changed": r.changed,
        "before": (r.prev_body or {}).get("description") if r.changed else None,
        "actions": actions,
        "order": (
            not (doc and doc.get("round") == r.draft_no),  # 2nf: changed rows lead
            ORDER.get(conf, 1),
            r.position,
            r.key,
        ),
    }


def metrics_ctx(s: Session, settings: Settings, now: dt.datetime) -> dict[str, Any]:
    rows_all = drafts.stored(s)
    rows = [r for r in rows_all if r.state != "skipped"]
    sugg: list[Any] = []
    if any(r.state == "needs_source" for r in rows):
        from othor.setup.connect import suggested, suggestion_for

        sugg = suggested(s)
    shown = sorted(
        (metric_row(r, suggestion_for(r, sugg) if sugg else None) for r in rows),
        key=lambda x: x["order"],
    )
    found = [
        {
            "key": d.key,
            "type": d.body["type"].capitalize(),
            "text": d.body["text"],
            "skipped": d.state == "skipped",
        }
        for d in drafts.stored(s, "discovery")
    ]
    meta = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "meta")).first()
    available, why = model_status(settings, s, now)
    c = confirm.counts(rows)
    by_rules = bool(meta and meta.body.get("by") == "rules")
    sources = sorted({(r.body.get("table") or {}).get("source", "") for r in rows} - {""})
    doc = document_card(s, meta)
    ready = c["confident"]
    summary = (
        f"{ready} ready · {c['check']} to check · {c['conflict']} conflict"
        f"{'s' if c['conflict'] != 1 else ''} to settle"
    )
    if doc is not None:  # 2nf
        brief = _brief_time(s)
        summary = (
            f"{ready} ready · {c['check'] + c['conflict']} to check, not live until settled"
            f" · first brief tomorrow at {brief}"
        )
    set_aside = sum(
        1 for r in rows_all if r.state == "skipped" and (r.origin or {}).get("set_aside")
    )
    checks = checks_ctx(s, rows_all, found)
    n_found = len(rows)
    blocking = checks["decisions"]["first_blocking"]
    n_block = sum(1 for d in checks["decisions"]["rows"] if d.get("blocks"))
    over = confirm.over_ceiling(s)
    footer = (
        f"{n_block} required decision{'s' if n_block != 1 else ''} left before you continue"
        if blocking
        else ("Drafting…" if confirm.drafting(s) else "Ready for your first run")
    )
    if over and not blocking:  # the ceiling (M15.1)
        footer = (
            f"Othor AI follows up to {service.max_metrics()} metrics · skip {over} "
            f"before you continue"
        )
    n_confirm = checks["confirm"]["n"]
    return {
        "checks": checks,
        "title": f"We found {n_found} metric{'s' if n_found != 1 else ''}.",
        "lede2": (
            (f"From your files, {_and(sources)}." if sources else "From your files.")
            + (
                " One decision needs you before your first run."
                if len([d for d in checks["decisions"]["rows"] if d.get("blocks")]) == 1
                else (
                    f" {len([d for d in checks['decisions']['rows'] if d.get('blocks')])} "
                    "decisions need you before your first run."
                    if blocking
                    else " Nothing else needs you now."
                )
            )
        ),
        "footer": footer,
        # owner's O1: the button says how many metrics continuing confirms
        "confirm_label": f"Confirm {n_confirm} and continue" if n_confirm else "Continue",
        "set_aside": set_aside,
        "doc": doc,
        "round_line": (
            f"Draft {doc['round']} · {doc['changed']} changed by your document" if doc else None
        ),
        "rows": shown,
        "found": found,
        "counts": c,
        "drafting": confirm.drafting(s),
        "started": meta is not None,
        "by_rules": by_rules,
        "model": available,
        "model_why": why,
        "no_model": NO_MODEL if by_rules else None,
        "can_retry": by_rules and available,
        "can_confirm": not c["conflict"]
        and not over
        and not confirm.drafting(s)
        and bool(rows)
        and not any(r.changed for r in rows),
        "summary": summary,
        "lede": (
            f"{len(rows)} metric{'s' if len(rows) != 1 else ''} suggested from your uploads"
            + (f", {_and(sources)}" if sources else "")
            + "."
        ),
        "changes": sum(1 for r in rows if r.changed),
    }


# --- M11c · Metrics as three checks (m1–m8) ---------------------------------------------------

SETUP_METRICS = "/setup/metrics"


def _source_line(r: SetupDraft) -> str:
    """Where a metric is read: the upload it was matched in, else source.table.column."""
    where = _where(r)
    if r.refs:
        return where[0] if where else ""
    t = r.body.get("table") or {}
    col = ((r.body.get("measure") or {}).get("expr") or {}).get("col")
    parts = [t.get("source"), t.get("table"), col if isinstance(col, str) else None]
    return ".".join(p for p in parts if p)


def _decision(r: SetupDraft, sugg: list[Any]) -> dict[str, Any] | None:
    """A draft that needs the owner's answer, or one they've answered (it stays listed)."""
    b = r.body
    name = b.get("name") or r.key
    grain = GRAIN.get(b.get("grain", "month"), "")
    base = {"key": r.key, "name": name, "grain": grain}
    val = r.value or {}
    if r.state == "skipped":
        if (r.origin or {}).get("set_aside") or b.get("status") != "needs_source":
            return None
        personal = (r.origin or {}).get("skipped_from") == "excluded_table"
        return {
            **base,
            "kind": "set_aside",
            "open": False,
            "required": False,
            "question": "Use it without personal columns?" if personal else "Where is it kept?",
            "done": "Set aside for now",
            "note": "It isn’t in your brief until you bring it back.",
            "change": "Bring it back",
        }
    if r.changed:
        return {
            **base,
            "kind": "changed",
            "open": True,
            "blocks": True,
            "required": True,
            "question": "Use the redrafted definition?",
            "before": (r.prev_body or {}).get("description") or "",
            "after": b.get("description") or "",
        }
    c = val.get("conflict") if r.confidence == "conflict" else None
    if c:
        unit = b.get("unit", "inr")
        opts = [
            {
                "id": o["id"],
                "label": o["label"],
                "sub": o["where"],
                "value": f"{c['month']} · {value_text(o['value'], unit)}",
            }
            for o in c["options"]
        ]
        opts.append(
            {
                "id": "both",
                "label": "Show both in the brief",
                "sub": "Each with its label",
                "value": "",
            }
        )
        return {
            **base,
            "kind": "conflict",
            "open": True,
            "blocks": True,
            "required": True,
            "question": c["question"].split(". Which")[0].rstrip(".") + ".",
            "ask": f"Which should {name.lower()} use?",
            "options": opts,
        }
    if val.get("settled"):
        st = val["settled"]
        redraft = st.get("choice") in ("accept", "reject")
        return {
            **base,
            "kind": "settled",
            "open": False,
            "required": True,
            "question": "Use the redrafted definition?"
            if redraft
            else f"Which should {name.lower()} use?",
            "done": st["text"],
            # Change opens it again (confirm.reopen): only an answer that kept what it replaced
            "change": "Change" if st.get("was") or st.get("prev") is not None else None,
        }
    if r.state == "needs_source":
        from othor.setup.connect import connect_label, suggestion_for

        sug = suggestion_for(r, sugg) if sugg else None
        # the model said why it couldn't draft it: ask how the number is got, not where it is
        # kept (the table may well be there; owner, 12 Nov 2026)
        reason = bool((r.origin or {}).get("reason"))
        question = (
            connect_label(sug) + "?"
            if sug
            else ("How do you get this number?" if reason else "Where is it kept?")
        )
        return {
            **base,
            "kind": "needs_source",
            "open": True,
            "blocks": False,
            "required": False,
            "question": question,
            "why": (b.get("needs") or r.note or "No connected source has it yet").rstrip(".")
            + ". Without it, this metric is set aside for now.",
            "connect": (connect_label(sug), connect_url(r.key, sug.kind)) if sug else None,
            "where": chat_url(r.key, "where"),
            "where_label": "Tell Othor AI" if reason else "Where is it?",
        }
    if r.state == "excluded_table":
        return {
            **base,
            "kind": "excluded",
            "open": True,
            "blocks": False,
            "required": False,
            "question": "Use it without personal columns?",
            "why": r.note or "Its table holds personal data, which is never read.",
        }
    return None


def confirm_item(r: SetupDraft, *, confirmed: bool | None = None) -> dict[str, Any]:
    """One row of Metrics to confirm (M11c m5): setup's panel, and the app's panel for the
    metrics a later source adds (6h). The value is computed, never written by a model."""
    b = r.body
    doc = (r.origin or {}).get("document") or {}
    return {
        # 2nf: a metric the owner's document changed carries a tag, the document's
        # note and its citation ("sales_dw data dictionary.pdf · p. 7")
        "doc_changed": bool(doc) and doc.get("round") == r.draft_no,
        "doc_note": doc.get("note") if doc.get("round") == r.draft_no else None,
        "cites": _where(r) if r.refs else [],
        "key": r.key,
        "name": b.get("name") or r.key,
        "grain": GRAIN.get(b.get("grain", "month"), ""),
        "definition": b.get("description") or "",
        "value": value_line(r),
        "source": _source_line(r),
        "confirmed": r.state == "confirmed" if confirmed is None else confirmed,
        "edit": chat_url(r.key, "edit"),
    }


def decisions_of(
    s: Session, rows_all: list[SetupDraft]
) -> tuple[list[dict[str, Any]], list[SetupDraft]]:
    """The decisions in their fixed order (#32): required first, then optional, each kind in the
    drafts' own order. A decision keeps its place once it's answered or set aside, so the step
    numbers don't shift as answers land. The rest of the rows are the metrics to confirm."""
    sugg: list[Any] = []
    if any(r.state == "needs_source" for r in rows_all):
        from othor.setup.connect import suggested

        sugg = suggested(s)
    decisions: list[dict[str, Any]] = []
    confirm_rows: list[SetupDraft] = []
    for r in rows_all:
        d = _decision(r, sugg)
        if d is not None:
            decisions.append(d)
        elif r.state != "skipped":
            confirm_rows.append(r)
    decisions.sort(key=lambda d: not d["required"])  # stable: the drafts' order within each
    for i, d in enumerate(decisions, 1):
        d["n"] = i
    return decisions, confirm_rows


def decision_url(key: str) -> str:
    """A decision's own URL in the panel (Back and Next link to it; refresh shows it)."""
    from urllib.parse import quote

    return f"{SETUP_METRICS}?panel=decisions&d={quote(key, safe='')}"


def decision_after(decisions: list[dict[str, Any]], key: str) -> str:
    """Where a decision moves on to once it's answered or set aside: the next one still open
    after it, then from the start; the page (the panel closes) when none is left."""
    keys = [d["key"] for d in decisions]
    at = keys.index(key) if key in keys else -1
    for d in decisions[at + 1 :] + decisions[: max(at, 0)]:
        if d["key"] != key and d["open"]:
            return decision_url(d["key"])
    return SETUP_METRICS


def decisions_panel(decisions: list[dict[str, Any]], key: str | None) -> dict[str, Any]:
    """One decision per screen (#32, the questions panel's style): "Decision 2 of 6 · 1 decided",
    one segment per decision (decided filled, current outlined), the decision, Back and Next
    (Done on the last, which closes the panel)."""
    if not decisions:
        return {"empty": True}
    keys = [d["key"] for d in decisions]
    if key in keys:
        at = keys.index(key)
    else:
        at = next((k for k, d in enumerate(decisions) if d["open"]), len(decisions) - 1)
    segs = [
        " ".join(c for c in ("" if d["open"] else "done", "now" if k == at else "") if c)
        for k, d in enumerate(decisions)
    ]
    return {
        "empty": False,
        "at": at + 1,
        "total": len(decisions),
        "decided": sum(1 for d in decisions if not d["open"]),
        "segs": segs,
        "x": decisions[at],
        "back": decision_url(keys[at - 1]) if at > 0 else None,
        "next": decision_url(keys[at + 1]) if at < len(keys) - 1 else None,
    }


def checks_ctx(
    s: Session, rows_all: list[SetupDraft], found: list[dict[str, Any]]
) -> dict[str, Any]:
    """The three cards and their panels (M11c): Assumptions (what the rules found), Decisions
    (only an open conflict or a redrafted definition blocks), Metrics to confirm. Every value
    shown is computed; none is written by a model."""
    decisions, confirm_rows = decisions_of(s, rows_all)
    blocking = [d for d in decisions if d.get("blocks")]
    groups: dict[str, list[dict[str, Any]]] = {"files": [], "databases": []}
    for r in confirm_rows:
        groups["files" if r.refs else "databases"].append(confirm_item(r))
    metrics = groups["files"] + groups["databases"]
    n_conf = sum(1 for m in metrics if m["confirmed"])
    used = [f for f in found if not f["skipped"]]
    first = blocking[0] if blocking else None
    if first is not None:
        dec_text = (
            f"{first['name']}: {first['question'][:1].lower()}{first['question'][1:]} "
            "Settle it to continue."
            if first["kind"] == "conflict"
            else f"{first['name']}: a redrafted definition to accept or keep."
        )
    elif decisions:
        done = [d for d in decisions if not d["open"]]
        dec_text = (
            " · ".join(f"{d['name']}: {d['done'][:1].lower()}{d['done'][1:]}" for d in done[:2])
            if done and len(done) == len(decisions)
            else f"{len(decisions) - len(done)} to look at; none blocks your first run."
        )
    else:
        dec_text = "Nothing to decide. Your files and sources agree."
    return {
        "assumptions": {
            "rows": found,
            "n": len(found),
            "good": True,
            "text": (
                "Used as drafted. Change any that are wrong."
                if len(used) == len(found)
                else f"{len(used)} used, {len(found) - len(used)} not used. "
                "Change any that are wrong."
            )
            if found
            else "Nothing assumed from your data.",
        },
        "decisions": {
            "rows": decisions,
            "n": len(decisions),
            "good": not blocking,
            "open": sum(1 for d in decisions if d["open"]),
            "text": dec_text,
            "first_blocking": first,
        },
        "confirm": {
            "files": groups["files"],
            "databases": groups["databases"],
            "n": len(metrics),
            "confirmed": n_conf,
            "remaining": [m["key"] for m in metrics if not m["confirmed"]],
            "good": True,
            "text": "Each one goes into your brief once it’s confirmed. Confirm them here, or "
            "all at once when you continue.",
        },
    }


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def stage_line(state: str, names: list[str], done: int, total: int, elapsed: int) -> str:
    """What drafting is doing now, so the page never looks frozen (#31): while Othor AI drafts,
    it names each file and source in turn (two seconds each), then the drafting itself; while the
    values are computed, the count. Names and counts only, never a made-up percentage."""
    if state == "computing" or total:
        return f"Computing each metric on your data · {fmt.num(done)} of {fmt.num(total)}"
    steps = [f"Reading {n}…" for n in names] + ["Othor AI is drafting your metrics…"]
    return steps[min(elapsed // 2, len(steps) - 1)] if elapsed < 2 * len(steps) else steps[-1]


def elapsed_text(seconds: int) -> str:
    """ "8 s", "1 min 05 s": how long drafting has run, shown from 3 s on."""
    if seconds < 3:
        return ""
    m, sec = divmod(seconds, 60)
    return f"{m} min {sec:02d} s" if m else f"{sec} s"


def progress_ctx(s: Session, now: dt.datetime | None = None) -> dict[str, Any]:
    """2m: each draft as it is computed; the special lines for excluded and missing."""
    rows = drafts.stored(s)
    meta = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "meta")).first()
    items = []
    for r in rows:
        if r.state == "excluded_table":
            items.append({"name": r.body.get("name"), "kind": "excluded", "note": r.note})
        elif r.state == "needs_source":
            items.append({"name": r.body.get("name"), "kind": "missing", "note": r.note})
        else:
            done = r.value is not None
            items.append(
                {
                    "name": r.body.get("name"),
                    "kind": "done" if done else "drafting",
                    "check": done and r.confidence in ("check", "conflict"),
                }
            )
    done = sum(1 for i in items if i["kind"] != "drafting")
    from othor.connectors.models import Source

    names = sorted(
        x.name
        for x in s.scalars(
            sa.select(Source).where(Source.deleted_at.is_(None), Source.status != "draft")
        )
    )
    from othor.setup.models import Upload

    state = (meta.body.get("state") if meta else "drafting") or "drafting"
    started = (meta.body.get("started") if meta else None) or None
    elapsed = 0
    if now is not None and started:
        elapsed = max(0, int((now - dt.datetime.fromisoformat(started)).total_seconds()))
    files = [
        u.filename
        for u in s.scalars(
            sa.select(Upload).where(Upload.deleted_at.is_(None)).order_by(Upload.filename)
        )
        if u.status != "unreadable"
    ]
    return {
        "items": items,
        "done": done,
        "total": len(items),
        "state": state,
        "source_names": _and(names) if names else "",
        "stage": stage_line(state, files + names, done, len(items), elapsed),
        "elapsed": elapsed_text(elapsed),
    }


# --- uploads and business ---------------------------------------------------------------------

DOT = {"read": "ok", "read_as_text": "ok", "needs_model": "warn", "unreadable": "neg"}


def uploads_ctx(s: Session) -> dict[str, Any]:
    ups = list(
        s.scalars(
            sa.select(Upload)
            .where(Upload.deleted_at.is_(None))
            .order_by(Upload.created_at, Upload.filename)
        )
    )
    files = [
        {
            "id": u.id,
            "name": u.filename,
            "line": uploads.summary_line(u),
            "reading": u.status in ("added", "reading"),
            "dot": DOT.get(u.status),
        }
        for u in ups
    ]
    read = sum(1 for u in ups if u.status in ("read", "read_as_text"))
    return {"files": files, "read": read, "reading": any(f["reading"] for f in files)}


def pending(s: Session, kind: str) -> bool:
    return (
        s.scalars(
            sa.select(Job).where(Job.kind == kind, Job.status.in_(("queued", "running")))
        ).first()
        is not None
    )


# --- 2nca / 2ncb · connect a source from Metrics -----------------------------------------------

CONNECT_STEPS = ["Choose", "Sign in", "Choose data", "Scan", "Review"]


def connect_ctx(
    s: Session, settings: Settings, row: SetupDraft, kind: str, sid: Any = None
) -> dict[str, Any]:
    """The M2 connect panel opened for one metric. Sheets only in v0.1's panel; a database
    is connected from the Sources step."""
    from othor.connectors.models import Source
    from othor.connectors.sheets.client import SHEETS_MIME
    from othor.setup import connect
    from othor.web import sources_view as sv

    src = None
    if sid:
        src = s.scalars(sa.select(Source).where(Source.id == sid)).first()
    if src is None:
        src = s.scalars(
            sa.select(Source)
            .where(Source.kind == kind, Source.deleted_at.is_(None))
            .order_by(Source.created_at.desc())
        ).first()
        if src is not None and connect.for_key(src) != row.key:
            src = None
    sug = connect.suggestion_for(row, connect.suggested(s, keep=src))
    name = sug.name if sug and sug.name else None
    ctx: dict[str, Any] = {
        "kind": "connect",
        "key": row.key,
        "source_kind": kind,
        "title": f"Connect the {name}" if name else "Connect Google Sheets",
        "subtitle": f"For {row.body.get('name') or row.key} · read-only",
        "close": "/setup/metrics",
        "steps": CONNECT_STEPS,
        "src": src,
        "mode": settings.sheets_mode,
        "sheets_mime": SHEETS_MIME,
        "google_api_key": settings.google_api_key.get_secret_value(),
        "google_app_id": settings.google_app_id,
    }
    if kind != "gsheets":
        return {**ctx, "step": 1, "database": True}
    if src is None:
        return {**ctx, "step": 2, "account": None, "rows": [], "picked": []}
    rows = sv.tab_rows(s, src)
    if src.status != "connected":
        view = sv.scan_view(s, src)
        return {
            **ctx,
            "step": 3,
            "account": (src.config or {}).get("account"),
            "rows": rows,
            "picked": (src.config or {}).get("files", []),
            "running": view.running,
            "selected": sum(1 for r in rows if r.tab.selected),
            "to_check": sum(1 for r in rows if r.tab.selected and r.needs_check),
        }
    rv = connect.review(s, src.id)
    card = sv.source_card(s, src, settings)
    target = metric_row(rv.target) if rv.target is not None else None
    return {
        **ctx,
        "step": 5,
        "card": card,
        "drafting": rv.drafting,
        "target": target,
        "target_value": value_line_of(rv.target.value or {}, rv.target.body.get("unit", ""))
        if rv.target is not None and rv.target.value
        else None,
        "added": [metric_row(r) for r in rv.added],
    }


# --- 2ne / 2nf · redraft with your document ------------------------------------------------------

RESULT_DOT = {
    "conflict_resolved": "ok",
    "mapped": "ok",
    "rule_added": "ok",
    "dimension_updated": "ok",
    "filter_added": "ok",
    "noted": "muted",
    "no_change": "muted",
}


def _brief_time(s: Session) -> str:
    from othor.core.models import Company

    co = s.scalars(sa.select(Company)).first()
    return co.brief_time if co is not None else "06:00"


def _pages(up: Upload) -> str | None:
    summ = up.summary or {}
    n = summ.get("pages") or summ.get("page")
    if n:
        return f"{fmt.num(n)} page{'s' if n != 1 else ''}"
    if summ.get("sections"):
        return f"{fmt.num(summ['sections'])} sections"
    return None


def _doc_line(up: Upload) -> str:
    if up.status in ("read", "read_as_text") and _pages(up):
        return f"Read · {_pages(up)}"
    return {
        "needs_model": "Can't be read without the model",
        "unreadable": "Couldn't read this file",
    }.get(up.status, "Reading…")


def document_card(s: Session, meta: SetupDraft | None) -> dict[str, Any] | None:
    """The document the metrics were last redrafted with (2nf's card), or None."""
    from othor.setup.models import SetupChange

    if meta is None or not meta.body.get("document"):
        return None
    up = s.scalars(
        sa.select(Upload).where(Upload.id == uuid.UUID(str(meta.body["document"])))
    ).first()
    if up is None:
        return None
    rnd = int(meta.body.get("round") or 2)
    changes = list(
        s.scalars(
            sa.select(SetupChange).where(
                SetupChange.upload_id == up.id, SetupChange.draft_round == rnd
            )
        )
    )
    changed = len({c.draft_key for c in changes if c.applied and c.draft_key})
    resolved = sum(1 for c in changes if c.result == "conflict_resolved" and c.applied)
    parts = [p for p in (_pages(up), "read") if p]
    parts.append(f"{changed} metric{'s' if changed != 1 else ''} changed")
    tail = f", {resolved} conflict{'s' if resolved != 1 else ''} resolved" if resolved else ""
    return {
        "round": rnd,
        "filename": up.filename,
        "tile": up.kind.upper()[:4],
        "line": " · ".join(parts) + tail,
        "changed": changed,
        "upload": up,
    }


def redraft_ctx(s: Session) -> dict[str, Any]:
    """2ne: the progress of a redraft with a document, and what the document changed."""
    from othor.setup.document import NOT_COVERED, RESULTS
    from othor.setup.models import SetupChange

    meta = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "meta")).first()
    body = (meta.body if meta else {}) or {}
    up = (
        s.scalars(sa.select(Upload).where(Upload.id == uuid.UUID(str(body["document"])))).first()
        if body.get("document")
        else None
    )
    pending = body.get("mode") == "document" and body.get("state") != "done"
    if up is None:  # queued: the newest document upload
        up = s.scalars(
            sa.select(Upload).where(Upload.deleted_at.is_(None)).order_by(Upload.created_at.desc())
        ).first()
    changes = []
    if up is not None and body.get("document") == str(up.id):
        for c in s.scalars(
            sa.select(SetupChange)
            .where(
                SetupChange.upload_id == up.id,
                SetupChange.draft_round == int(body.get("round") or 2),
            )
            .order_by(SetupChange.created_at)
        ):
            changes.append(
                {
                    "subject": c.subject,
                    "quote": f"“{c.quote}”" if c.quote else NOT_COVERED,
                    "locator": c.locator,
                    "result": RESULTS.get(c.result, c.result),
                    "dot": RESULT_DOT.get(c.result, "muted"),
                }
            )
    prog = progress_ctx(s)
    return {
        "doc": {
            "filename": up.filename if up else "",
            "tile": (up.kind.upper()[:4] if up else "DOC"),
            "line": _doc_line(up) if up else "Reading…",
        },
        "changes": changes,
        "pending": pending,
        "done": prog["done"],
        "total": prog["total"],
    }
