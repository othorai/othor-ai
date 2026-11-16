# SPDX-License-Identifier: Apache-2.0
"""Answers to Home questions (3aa, M10a): one at a time, quick replies, free text, Skip, Undo.

A quick reply maps to a typed Ontology record by the question's kind, with no model. Free text is
drafted into a record by the model (`home_answer`), validated against the record's schema and the
numbers guard; a plant name for an unmapped item code needs no model. Each answer writes one
Ontology version (from the next run); Undo writes a version putting the records back.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core import fmt
from othor.core.events import record_event
from othor.ontology import service
from othor.ontology.models import OntologyRecord, Question
from othor.ontology.types import validate_body

SKIP_DAYS = 7
NOT_AVAILABLE = "Not available right now · use the replies above"
UNTIL = ("End of this month", "End of next month", "Not sure, check again in 30 days")
LOOK_INTO_IT = {"No, look into it", "Not sure, look into it", "We don't have this"}
PROMPT = ("home_answer", "v2")  # v2: body as body_json (structured output, 12 Nov 2026)


@dataclass
class Outcome:
    saved: bool = False
    drafted: bool = False  # a model draft waits for Apply (non-negotiable 6)
    follow_up: list[str] = field(default_factory=list)  # chips for a second step
    note: str | None = None  # a line from Othor AI with no record (asks for more)


# --- the queue -------------------------------------------------------------------------------


def queue(s: Session, today: dt.date) -> list[Question]:
    """Open questions, and skipped ones whose week is up, in Home's order."""
    rows = s.scalars(
        sa.select(Question).where(
            sa.or_(
                Question.status == "open",
                sa.and_(Question.status == "skipped", Question.skipped_until <= today),
            )
        )
    ).all()
    return sorted(rows, key=_order_key)


def _order_key(q: Question) -> tuple[str, Any, str]:
    return (q.rank_key or "9", q.created_at, str(q.id))


def is_open(q: Question, today: dt.date) -> bool:
    """Waiting for an answer now: open, or set aside and its week is up."""
    return q.status == "open" or (
        q.status == "skipped" and q.skipped_until is not None and q.skipped_until <= today
    )


def set_aside_today(q: Question, today: dt.date) -> bool:
    """Ask me later pressed today (its week runs from today)."""
    return q.status == "skipped" and q.skipped_until == today + dt.timedelta(days=SKIP_DAYS)


def set_aside(s: Session, today: dt.date) -> list[Question]:
    """Questions set aside with Ask me later whose week isn't up: still waiting on the owner (the
    checks they block stay blocked), so Home and the Ontology keep counting them (12 Nov 2026)."""
    rows = s.scalars(
        sa.select(Question).where(Question.status == "skipped", Question.skipped_until > today)
    ).all()
    return sorted(rows, key=_order_key)


def todays_order(s: Session, today: dt.date, tz: Any) -> list[Question]:
    """The questions panel's order (v0.11, docs/plans/questions-panel.md part 1): today's
    questions in Home's order, those answered or set aside today keeping their place, so the
    numbering doesn't shift as answers land. Computed, so a refresh or a shared link agrees."""
    rows = s.scalars(
        sa.select(Question).where(Question.status.in_(("open", "answered", "skipped")))
    ).all()
    keep = [
        q
        for q in rows
        if is_open(q, today)
        or q.status == "skipped"  # set aside: kept in the panel, answerable any time (12 Nov)
        or (
            q.status == "answered"
            and q.answered_at is not None
            and q.answered_at.astimezone(tz).date() == today
        )
    ]
    return sorted(keep, key=_order_key)


def answered_on(s: Session, day: dt.date, tz: Any) -> list[Question]:
    """Questions answered today (company time), oldest first: the chat's thread so far."""
    rows = s.scalars(sa.select(Question).where(Question.status == "answered")).all()
    out = [q for q in rows if q.answered_at and q.answered_at.astimezone(tz).date() == day]
    return sorted(out, key=lambda q: q.answered_at or dt.datetime.min.replace(tzinfo=dt.UTC))


# --- quick replies ---------------------------------------------------------------------------


def _slug(v: str, n: int = 40) -> str:
    return "".join(c if c.isalnum() else "_" for c in v.lower())[:n]


def _month_end(d: dt.date, ahead: int = 0) -> dt.date:
    y, m = d.year + (d.month - 1 + ahead) // 12, (d.month - 1 + ahead) % 12 + 1
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def until_date(choice: str, today: dt.date) -> dt.date | None:
    if choice == UNTIL[0]:
        return _month_end(today)
    if choice == UNTIL[1]:
        return _month_end(today, 1)
    if choice == UNTIL[2]:
        return today + dt.timedelta(days=30)
    return None


def _record(s: Session, type_: str, key: str) -> OntologyRecord | None:
    return next((r for r in service.active(s, [type_]) if r.key == key), None)


def _pause(q: Question, until: dt.date, today: dt.date, name: str | None = None) -> Any:
    sub = q.subject or {}
    entity = str(sub.get("entity") or "")
    start = dt.date.fromisoformat(str(sub.get("since") or today.isoformat()))
    body = {
        "name": (name or f"{entity} paused")[:200],
        "window": {"kind": "range", "start": start.isoformat(), "end": until.isoformat()},
        "entity": entity,
        "answered_on": today.isoformat(),
    }
    return service.put("known_pattern", "pause_" + _slug(entity), body, _ref(q))


def _ref(q: Question) -> dict[str, Any]:
    return {"answer": str(q.id)}


def _answer(q: Question, text: str) -> Any:
    body = {"question": q.text[:500], "answer": text[:500]}
    return service.put("answer", "answer_" + q.fingerprint[:40], body, _ref(q))


def _plant(s: Session, q: Question, plant: str) -> Any | None:
    sub = q.subject or {}
    prefix = str(sub.get("prefix") or "")
    vocab = next(
        (r for r in service.active(s, ["vocabulary"]) if r.body.get("kind") == "prefix_map"), None
    )
    if vocab is None or not prefix:
        return None
    entries = {**vocab.body.get("entries", {}), prefix: plant}
    return service.put("vocabulary", vocab.key, {**vocab.body, "entries": entries}, _ref(q))


def changes_for_reply(
    s: Session, q: Question, choice: str, today: dt.date
) -> tuple[list[Any], Outcome, str]:
    """(changes, outcome, the answer as the owner gave it). No changes and a follow-up or note
    when the reply needs a second step."""
    if choice in LOOK_INTO_IT:
        return [_answer(q, choice)], Outcome(saved=True), choice
    if q.kind == "not_determinable" and choice == "Yes, expected":
        return [], Outcome(follow_up=list(UNTIL), note="Until when?"), choice
    if q.kind == "not_determinable" and (until := until_date(choice, today)):
        return [_pause(q, until, today)], Outcome(saved=True), choice
    if q.kind == "pattern":
        rec = _record(s, "known_pattern", str((q.subject or {}).get("pattern")))
        if choice == "Yes, every year" and rec is not None:
            body = {**rec.body, "confirmed": True, "answered_on": today.isoformat()}
            return [service.put("known_pattern", rec.key, body, _ref(q))], Outcome(True), choice
        if choice == "No, the dates change":
            return [], Outcome(note="Type the dates it runs, below."), choice
    if q.kind == "unmapped_plant":
        if choice == "Another plant":
            return [], Outcome(note="Type the plant's name below."), choice
        c = _plant(s, q, choice)
        return ([c], Outcome(saved=True), choice) if c else ([], Outcome(), choice)
    if q.kind == "needs_confirmation":
        side = choice.removesuffix(" is right")
        if side in ((q.subject or {}).get("sides") or []):
            return [_answer(q, choice)], Outcome(saved=True), side
    return [], Outcome(), choice


# --- writing an answer -----------------------------------------------------------------------


def _save(
    s: Session,
    q: Question,
    changes: list[Any],
    answer: str,
    *,
    actor: str,
    now: dt.datetime,
) -> bool:
    v = service.apply(
        s, changes, actor=actor, reason=f"Answered on Home: {q.text}"[:300], allow_empty=False
    )
    c = changes[0]
    rec = _record(s, c.type, c.key)
    q.status, q.answered_at, q.answer_text = "answered", now, answer[:500]
    q.answer_version = v.number if v is not None else None
    q.answer_record_id = rec.id if rec is not None else None
    q.skipped_until = None
    record_event(
        s,
        actor=actor,
        action="question.answered",
        object_type="question",
        object_id=q.id,
        before={"status": "open"},
        after={"answer": answer[:200], "version": q.answer_version, "record": f"{c.type}:{c.key}"},
    )
    return True


def reply(
    s: Session, q: Question, choice: str, *, actor: str, now: dt.datetime, today: dt.date
) -> Outcome:
    if q.status == "answered":
        return Outcome()
    changes, out, answer = changes_for_reply(s, q, choice, today)
    if changes:
        _save(s, q, changes, answer, actor=actor, now=now)
    return out


def skip(s: Session, q: Question, *, actor: str, today: dt.date) -> None:
    q.status, q.skipped_until = "skipped", today + dt.timedelta(days=SKIP_DAYS)
    record_event(
        s,
        actor=actor,
        action="question.skipped",
        object_type="question",
        object_id=q.id,
        after={"until": q.skipped_until.isoformat()},
    )


def undo(s: Session, q: Question, *, actor: str) -> None:
    if q.status != "answered":
        return
    version = None
    if q.answer_version is not None:
        nv = service.undo(s, q.answer_version, actor=actor)
        version = nv.number if nv is not None else None
    q.status, q.answered_at, q.answer_text = "open", None, None
    q.answer_version, q.answer_record_id = None, None
    record_event(
        s,
        actor=actor,
        action="question.undone",
        object_type="question",
        object_id=q.id,
        before={"status": "answered"},
        after={"status": "open", "version": version},
    )


# --- free text -------------------------------------------------------------------------------

PLANT = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,39}$")
DRAFT_TYPES = {
    "not_determinable": {"known_pattern"},
    "pattern": {"known_pattern"},
    "needs": {"owner", "rule"},
}


def calendar_inputs(today: dt.date) -> dict[str, Any]:
    """Dates the model may use, as data: the year now and next, month numbers, days. A date in
    a draft is grounded by these (the numbers guard); no business figure is."""
    return {
        "today": today.isoformat(),
        "years": [today.year, today.year + 1],
        "months": {calendar.month_abbr[i]: f"{i:02d}" for i in range(1, 13)},
        "days": [f"{d:02d}" for d in range(1, 32)],
        "day_numbers": list(range(1, 32)),
    }


def model_inputs(s: Session, q: Question, text: str, today: dt.date) -> dict[str, Any]:
    sub = dict(q.subject or {})
    sub.pop("money", None)  # no figures: the model words a record, it never computes
    current = None
    if q.kind == "pattern":
        rec = _record(s, "known_pattern", str(sub.get("pattern")))
        current = dict(rec.body) if rec else None
    return {
        "question": q.text,
        "why": q.why,
        "kind": q.kind,
        "subject": sub,
        "current_record": current,
        "answer": text,
        "allowed_types": sorted(DRAFT_TYPES.get(q.kind, set())),
        "metrics": (
            [
                {"key": r.key, "name": r.body.get("name", r.key)}
                for r in service.active(s, ["metric"])
            ]
            if q.kind == "needs"
            else []
        ),
        "calendar": calendar_inputs(today),
    }


def _name_grounded(name: str, said: str, window: dict[str, Any]) -> bool:
    """A drafted name may hold only numbers the owner wrote or the window's own dates ("until
    1 Dec"): the calendar grounds dates, never a count or an amount."""
    from othor.ai.guards import numerals

    allowed = numerals(said)
    for k in ("start", "end"):
        v = str(window.get(k) or "")
        try:
            d = dt.date.fromisoformat(v)
        except ValueError:
            continue
        allowed |= {str(d.day), str(d.year), f"{d.day:02d}"}
    return numerals(name) <= allowed


def record_body(rec: dict[str, Any]) -> dict[str, Any]:
    """The record's body: `body_json` (v2, a JSON object as a string) or `body` (v1)."""
    import json

    raw = rec.get("body_json")
    if isinstance(raw, str) and raw.strip():
        try:
            got = json.loads(raw)
        except ValueError:
            return {}
        return got if isinstance(got, dict) else {}
    return dict(rec.get("body") or {})


def draft_changes(
    s: Session, q: Question, out: dict[str, Any] | None, today: dt.date, said: str = ""
) -> list[Any] | None:
    """The model's draft as a validated change, or None."""
    if not out or not isinstance(out.get("record"), dict):
        return None
    rec = out["record"]
    type_ = rec.get("type")
    if type_ not in DRAFT_TYPES.get(q.kind or "", set()):
        return None
    body = record_body(rec)
    key = str(rec.get("key") or "")
    if q.kind == "not_determinable":
        entity = str((q.subject or {}).get("entity") or "")
        if body.get("entity") != entity:
            return None
        key = "pause_" + _slug(entity)
        body["answered_on"] = today.isoformat()
    if q.kind == "pattern":
        key = str((q.subject or {}).get("pattern"))
        cur = _record(s, "known_pattern", key)
        if cur is None:
            return None
        body = {**cur.body, **body, "confirmed": True, "answered_on": today.isoformat()}
    if q.kind == "needs":
        if type_ == "rule" and body.get("kind") != "pair":
            return None
        name = body.get("name") if type_ == "owner" else "_".join(body.get("over") or [])
        key = (type_ + "_" + _slug(str(name or ""), 60)).rstrip("_")
    if type_ == "known_pattern" and not _name_grounded(
        str(body.get("name", "")), said, dict(body.get("window") or {})
    ):
        return None
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,80}", key):
        return None
    try:
        body = validate_body(type_, body)
    except ValueError:
        return None
    return [service.put(type_, key, body, _ref(q))]


def free_text(
    s: Session,
    q: Question,
    text: str,
    *,
    actor: str,
    now: dt.datetime,
    today: dt.date,
    draft: Any,
) -> Outcome:
    """`draft(inputs) -> dict | None`: the model call (with the numbers guard applied by the
    caller), or None when no model is available."""
    from othor.ai.guards import numbers_grounded

    text = text.strip()[:500]
    if not text or q.status == "answered":
        return Outcome()
    if q.kind == "unmapped_plant":
        if not PLANT.match(text):
            return Outcome(note="Type the plant's name only.")
        c = _plant(s, q, text)
        if c is None:
            return Outcome(note=NOT_AVAILABLE)
        _save(s, q, [c], text, actor=actor, now=now)
        return Outcome(saved=True)
    if q.kind == "needs_confirmation":
        return Outcome(note="Choose one of the replies above.")
    inputs = model_inputs(s, q, text, today)
    out = draft(inputs)
    if out is None:
        return Outcome(note=NOT_AVAILABLE)
    import json

    if not numbers_grounded(json.dumps(out, ensure_ascii=False), inputs):
        return Outcome(note=NOT_AVAILABLE)
    changes = draft_changes(s, q, out, today, text)
    if changes is None:
        return Outcome(note=NOT_AVAILABLE)
    # drafted by the model: nothing changes until the owner applies it (non-negotiable 6)
    q.draft = {
        "text": text,
        "changes": [{"op": c.op, "type": c.type, "key": c.key, "body": c.body} for c in changes],
    }
    record_event(
        s,
        actor=actor,
        action="question.drafted",
        object_type="question",
        object_id=q.id,
        after={"records": [f"{c.type}:{c.key}" for c in changes]},
    )
    return Outcome(drafted=True)


def apply_draft(s: Session, q: Question, *, actor: str, now: dt.datetime) -> bool:
    """Apply the drafted answer: the record(s) as one Ontology version, as a quick reply would."""
    d = q.draft or {}
    if q.status == "answered" or not d.get("changes"):
        return False
    changes = [
        service.put(c["type"], c["key"], c["body"], _ref(q))
        if c.get("op", "put") == "put"
        else service.retire(c["type"], c["key"])
        for c in d["changes"]
    ]
    q.draft = None
    return _save(s, q, changes, str(d.get("text") or ""), actor=actor, now=now)


# --- suggested answers and Let Othor AI decide (v0.12) ----------------------------------------

PICK = "s:"  # a suggested card's radio value: "s:<card key>"
DECIDED_BY = "othor_ai"
DECIDED_TAG = "Decided by Othor AI"
DECIDED_CONFIDENCE = "medium"  # capped until the owner confirms (non-negotiable 6)


def suggestions(q: Question) -> list[dict[str, Any]]:
    """The suggested answers stored on a needs question by the nightly questions step (code
    found them; the panel never calls a model to show them)."""
    if q.kind != "needs":
        return []
    return [c for c in (q.subject or {}).get("suggestions") or [] if isinstance(c, dict)]


def decision(q: Question) -> dict[str, Any]:
    """What Let Othor AI decide does for this question: {pick, why, next, href}."""
    return dict((q.subject or {}).get("decide") or {}) if q.kind == "needs" else {}


def _card(q: Question, key: str) -> dict[str, Any] | None:
    return next((c for c in suggestions(q) if c.get("key") == key), None)


def _card_change(c: dict[str, Any]) -> dict[str, Any] | None:
    ch = c.get("change") or {}
    try:
        body = validate_body(str(ch.get("type")), dict(ch.get("body") or {}))
    except ValueError:
        return None
    return {"op": "put", "type": ch["type"], "key": ch["key"], "body": body}


def pick_suggestion(s: Session, q: Question, key: str, *, actor: str) -> Outcome:
    """A picked card drafts its record: shown "Draft · not applied yet" with Apply, as a typed
    answer is. No model: the card was found and worded by code."""
    c = _card(q, key)
    if q.status == "answered" or c is None:
        return Outcome(note="Choose one of the replies above." if c is None else None)
    ch = _card_change(c)
    if ch is None:
        return Outcome(note=NOT_AVAILABLE)
    q.draft = {"text": str(c.get("label") or ""), "changes": [ch], "picked": key}
    record_event(
        s,
        actor=actor,
        action="question.drafted",
        object_type="question",
        object_id=q.id,
        after={"records": [f"{ch['type']}:{ch['key']}"], "suggestion": key},
    )
    return Outcome(drafted=True)


def decide(s: Session, q: Question, *, actor: str, now: dt.datetime) -> bool:
    """Let Othor AI decide: the owner's click is the apply for this one question (non-negotiable
    6, v0.12). The pick is the top card when its computed evidence clears the bar, decided at
    night by code; it is written tagged "Decided by Othor AI" with capped confidence and its
    evidence, listed on the Ontology to confirm or fix. False when Othor AI can't decide (the
    panel says why and the next step)."""
    if q.status == "answered":
        return False
    pick = decision(q).get("pick")
    c = _card(q, str(pick)) if pick else None
    ch = _card_change(c) if c else None
    if c is None or ch is None:
        record_event(
            s,
            actor=actor,
            action="question.decide_declined",
            object_type="question",
            object_id=q.id,
            after={"why": str(decision(q).get("why") or "")[:300]},
        )
        return False
    ref = {
        **_ref(q),
        "decided_by": DECIDED_BY,
        "evidence": str(c.get("sub") or "")[:300],
        "confidence": DECIDED_CONFIDENCE,
        "decided_on": now.date().isoformat(),
    }
    q.draft = None
    _save(
        s,
        q,
        [service.put(ch["type"], ch["key"], ch["body"], ref)],
        f"{DECIDED_TAG}: {c.get('label', '')}",
        actor=actor,
        now=now,
    )
    record_event(
        s,
        actor=actor,
        action="question.decided_by_ai",
        object_type="question",
        object_id=q.id,
        after={
            "record": f"{ch['type']}:{ch['key']}",
            "suggestion": c.get("key"),
            "evidence": ref["evidence"],
            "confidence": DECIDED_CONFIDENCE,
            "version": q.answer_version,
        },
    )
    return True


def decided_records(s: Session) -> list[OntologyRecord]:
    """Live records Othor AI decided that the owner hasn't confirmed yet."""
    # M15.1: filtered in the database (SQLite and PostgreSQL alike), not over every live record
    q = (
        sa.select(OntologyRecord)
        .where(
            OntologyRecord.status == "active",
            OntologyRecord.source_ref["decided_by"].as_string() == DECIDED_BY,
        )
        .order_by(OntologyRecord.type, OntologyRecord.key)
    )
    return list(s.scalars(q))


def confirm_decision(s: Session, r: OntologyRecord, *, actor: str) -> Any:
    """Confirm: the record becomes the owner's (the tag goes, the cap lifts from the next run);
    the same meaning, a new citation (one version)."""
    ref = {k: v for k, v in (r.source_ref or {}).items() if k not in ("decided_by", "confidence")}
    ref["confirmed_by_owner"] = (r.source_ref or {}).get("decided_on") or True
    v = service.apply(
        s,
        [service.put(r.type, r.key, dict(r.body), ref)],
        actor=actor,
        reason=f"Confirmed a decision by Othor AI: {record_line(r)}"[:300],
    )
    record_event(
        s,
        actor=actor,
        action="ontology.decision_confirmed",
        object_type="ontology_record",
        object_id=r.id,
        after={"record": f"{r.type}:{r.key}", "version": v.number if v else None},
    )
    return v


def discard_draft(s: Session, q: Question, *, actor: str) -> str:
    """Edit: the draft goes, its text comes back to the box."""
    text = str((q.draft or {}).get("text") or "")
    if q.draft is not None:
        q.draft = None
        record_event(
            s,
            actor=actor,
            action="question.draft_discarded",
            object_type="question",
            object_id=q.id,
        )
    return text


def draft_lines(s: Session, q: Question) -> list[str]:
    """The drafted change in plain words, for the panel's draft card."""
    out = []
    for c in (q.draft or {}).get("changes", []):
        rec = OntologyRecord(type=c["type"], key=c["key"], body=c["body"])
        out.append(record_line(rec))
    return out


def draft_section(q: Question) -> str:
    """The Ontology section a drafted answer goes into (the draft card's kicker)."""
    from othor.home.changes import SECTION

    first = ((q.draft or {}).get("changes") or [{}])[0]
    return SECTION.get(str(first.get("type") or ""), "NAMES AND WORDS")


def draft_effect(q: Question) -> list[str]:
    """What the drafted answer does once applied, said from its record (a template)."""
    out = []
    for c in (q.draft or {}).get("changes", []):
        rec = OntologyRecord(type=c["type"], key=c["key"], body=c["body"])
        out.append(confirmation(q, rec))
    return out


# --- what the chat shows ----------------------------------------------------------------------


def record_line(r: OntologyRecord | None) -> str:
    """'Known pause: Skarfab, 9 Oct – 1 Dec': the saved record as one line, from its body."""
    if r is None:
        return ""
    b = r.body
    if r.type == "known_pattern":
        w = b.get("window") or {}
        if b.get("entity") and w.get("kind") == "range":
            a, z = (dt.date.fromisoformat(w[k]) for k in ("start", "end"))
            return f"Known pause: {b['entity']}, {fmt.day_month(a)} – {fmt.day_month(z)}"
        return f"Known pattern: {b.get('name', r.key)}"
    if r.type == "vocabulary" and b.get("kind") == "prefix_map":
        return f"{b.get('term', 'Plants')}: " + ", ".join(
            f"{p} {v}" for p, v in sorted(b.get("entries", {}).items())
        )
    if r.type == "answer":
        return f"Your answer: {b.get('answer', '')}"
    if r.type == "owner":
        return f"Owner: {b.get('name', '')}" + (f", {b['role']}" if b.get("role") else "")
    if r.type == "rule":
        return f"Rule: {b.get('text') or b.get('kind', '')}"
    if r.type == "breakdown" and b.get("entity"):
        e = b["entity"]
        t = e.get("table") or {}
        active = ", ".join(e.get("active_values") or [])
        return f"Customer list: {t.get('table', '')} in {t.get('source', '')}" + (
            f", active when {e.get('status_column')} is {active}" if active else ""
        )
    if r.type == "metric" and b.get("distribution"):
        return f"Single values: {b.get('name', r.key)}, one per {b['distribution'].get('key')}"
    return f"{r.type.replace('_', ' ').capitalize()}: {r.key}"


def confirmation(q: Question, r: OntologyRecord | None) -> str:
    """Othor AI's line after an answer: what changes from the next run (a template)."""
    if r is None:
        return "Saved."
    b = r.body
    if r.type == "known_pattern" and b.get("entity"):
        end = dt.date.fromisoformat(b["window"]["end"])
        return f"Sweep won't flag {b['entity']} until {fmt.day_month(end)}."
    if r.type == "known_pattern":
        return "Sweep won't flag these dates from the next run."
    if r.type == "vocabulary":
        return "The plant split includes these items from the next run."
    if r.type == "answer" and q.kind == "needs_confirmation":
        return "Noted. Sweep keeps checking these records."
    if r.type == "answer":
        return "Noted. Sweep keeps flagging it."
    kind = b.get("kind") if r.type == "rule" else r.type
    return NEEDS_EFFECT.get(str(kind), "Saved. It applies from the next run.")


# what a needs record makes Sweep do, from the next run (a template)
NEEDS_EFFECT = {
    "pair": "Sweep watches these two numbers together from the next run.",
    "reconcile": "Sweep checks these records match from the next run.",
    "threshold": "Sweep checks for amounts bunching under this limit from the next run.",
    "breakdown": "Sweep watches each active customer from the next run.",
    "metric": "Sweep looks at each single value from the next run.",
}
