# SPDX-License-Identifier: Apache-2.0
"""The Audit log's reader (0.35): who did what, and when, newest first.

Reads `event` inside the caller's tenant scope (the `ix_event_company_at` index), plus the
model call log as "Othor AI wrote …" rows: the task, the prompt id and the guard's result,
never the text. Pages of 50, keyset on (at, id). Before and after are shown through `safe`,
which drops any key named like a password, token, secret or key."""

from __future__ import annotations

import contextlib
import datetime as dt
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.audit import words
from othor.core import actors, fmt
from othor.core.models import AppUser, Event, Membership

PAGE = 50
CSV_LIMIT = 20_000
SECRET = re.compile(r"pass(word|wd)?|token|secret|key|credential|cookie", re.I)
HIDDEN_KEYS = {"drafted_by"}  # said in the sentence instead
RANGES: list[tuple[str, str]] = [
    ("7", "Last 7 days"),
    ("30", "Last 30 days"),
    ("90", "Last 90 days"),
    ("all", "Any time"),
]
WHO_GROUPS: list[tuple[str, str]] = [
    ("ai", "Othor AI"),
    ("system", "System"),
    ("support", "Othor support"),
]


def safe(value: Any) -> Any:
    """Before / after with every key named like a password, token, secret or key dropped, at any
    depth (on top of the rule that credentials never go into events)."""
    if isinstance(value, Mapping):
        return {str(k): safe(v) for k, v in value.items() if not SECRET.search(str(k))}
    if isinstance(value, list | tuple):
        return [safe(v) for v in value]
    return value


@dataclass(frozen=True)
class Filters:
    who: str = ""  # "" everyone · ai · system · support · a user id
    kind: str = ""  # "" every kind · a words.KINDS key
    days: str = "7"  # a RANGES key

    @classmethod
    def of(cls, who: str | None, kind: str | None, days: str | None) -> Filters:
        w = (who or "").strip()
        if w not in {k for k, _ in WHO_GROUPS} and actors._uuid(w) is None:
            w = ""
        k = (kind or "").strip()
        k = k if k in words.KIND_LABEL else ""
        d = (days or "7").strip()
        d = d if d in {x for x, _ in RANGES} else "7"
        return cls(w, k, d)

    def query(self) -> str:
        return f"who={self.who}&kind={self.kind}&days={self.days}"


@dataclass
class Row:
    id: str
    at: dt.datetime
    when: str
    who: str
    kind: str
    what: list[tuple[str, str | None]]  # (text, link) parts, the object linked in the sentence
    details: list[tuple[str, str, str]] = field(default_factory=list)  # (what, before, after)
    note: str = ""  # a line under the details (a run's numbers, Othor AI's draft)
    links: list[tuple[str, str]] = field(default_factory=list)  # (label, href) under the details

    @property
    def text(self) -> str:
        return "".join(t for t, _ in self.what)


@dataclass
class Page:
    rows: list[Row]
    more: str | None  # the next page's cursor


# --- filters in SQL ------------------------------------------------------------------------------

_SUPPORT_RAW = sorted(actors._SUPPORT_WORDS)
_SYSTEM_RAW = sorted(actors._SYSTEM_WORDS)


def _who_clause(who: str, by_email: Mapping[str, str]) -> sa.ColumnElement[bool] | None:
    a = Event.actor
    if not who:
        return None
    ai_made = Event.action.in_(sorted(words.AI_MADE))
    if who == "ai":
        return sa.or_(a.like("othor_ai%"), ai_made)
    if who == "system":
        return sa.or_(a.in_(_SYSTEM_RAW), a == "")
    if who == "support":
        others = sa.and_(
            ~a.like("%:%"),
            ~a.like("%@%"),
            ~a.like("%-%-%-%-%"),
            a.notin_([*_SYSTEM_RAW, "owner", ""]),
        )
        return sa.or_(a.in_(_SUPPORT_RAW), others)
    emails = [e for e, uid in by_email.items() if uid == who]
    return sa.and_(a.in_([who, f"user:{who}", *emails]), ~ai_made)


def _kind_clause(kind: str) -> sa.ColumnElement[bool] | None:
    if not kind:
        return None
    act = Event.action
    mine = [a for a, (k, _) in words.WORDS.items() if k == kind]
    others = [a for a, (k, _) in words.WORDS.items() if k != kind]
    likes = [
        act.like(p.replace("_", r"\_") + "%", escape="\\")
        for p, (k, _) in words.FAMILIES.items()
        if k == kind
    ]
    likes += [
        act.like(p.replace("_", r"\_") + ".%", escape="\\")
        for p, k in words.PREFIX_KIND.items()
        if k == kind
    ]
    loose = sa.and_(sa.or_(*likes), act.notin_(others)) if likes else sa.false()
    return sa.or_(act.in_(mine), loose)


def _since(days: str, now: dt.datetime) -> dt.datetime | None:
    if days == "all":
        return None
    return now - dt.timedelta(days=int(days))


def _cursor(raw: str | None) -> tuple[dt.datetime, uuid.UUID] | None:
    if not raw or "|" not in raw:
        return None
    at, _, rid = raw.partition("|")
    try:
        return _utc(dt.datetime.fromisoformat(at)), uuid.UUID(rid)
    except ValueError:
        return None


def _utc(t: dt.datetime) -> dt.datetime:
    """Aware UTC: what the columns take and hand back (a naive time is UTC)."""
    return t.astimezone(dt.UTC) if t.tzinfo else t.replace(tzinfo=dt.UTC)


# --- people --------------------------------------------------------------------------------------


@dataclass
class People:
    names: dict[str, str]  # user id → name (or email)
    by_email: dict[str, str]  # email → user id


def people(s: Session, raws: Iterable[str] = ()) -> People:
    """Everyone the rows name: the company's members, and anyone else an actor points at (a
    former member, an old email actor)."""
    ids: set[uuid.UUID] = set(s.scalars(sa.select(Membership.user_id)))
    emails: set[str] = set()
    for raw in raws:
        a = actors.parse(raw)
        if a.user_id:
            ids.add(uuid.UUID(a.user_id))
        if a.email:
            emails.add(a.email)
    users: list[AppUser] = []
    if ids:
        users += list(s.scalars(sa.select(AppUser).where(AppUser.id.in_(ids))))
    if emails:
        users += list(s.scalars(sa.select(AppUser).where(sa.func.lower(AppUser.email).in_(emails))))
    return People(
        names={str(u.id): u.name or u.email for u in users},
        by_email={u.email.lower(): str(u.id) for u in users},
    )


def who_options(s: Session) -> list[tuple[str, str]]:
    members = s.scalars(
        sa.select(AppUser)
        .join(Membership, Membership.user_id == AppUser.id)
        .order_by(sa.func.lower(sa.func.coalesce(AppUser.name, AppUser.email)))
    ).all()
    return [
        ("", "Everyone"),
        *((str(u.id), u.name or u.email) for u in members),
        *WHO_GROUPS,
    ]


def kind_options() -> list[tuple[str, str]]:
    return [("", "All kinds"), *words.KINDS]


# --- objects -------------------------------------------------------------------------------------


def _objects(s: Session, events: list[Event]) -> dict[tuple[str, str], tuple[str, str]]:
    """(object_type, object_id) → (label, link) for the objects that have a screen."""
    from othor.briefs.models import Brief, BriefIssue
    from othor.connectors.models import Source
    from othor.engine.models import Run
    from othor.ontology.models import OntologyVersion

    def ids(kind: str) -> set[uuid.UUID]:
        out = set()
        for e in events:
            if e.object_type == kind:
                with contextlib.suppress(ValueError):
                    out.add(uuid.UUID(e.object_id))
        return out

    got: dict[tuple[str, str], tuple[str, str]] = {}
    if want := ids("source"):
        for src in s.scalars(sa.select(Source).where(Source.id.in_(want))):
            got[("source", str(src.id))] = (src.display_name or src.name, f"/sources/{src.id}")
    issue_ids = ids("brief_issue")
    issue_brief: dict[str, str] = {}
    if issue_ids:
        for iss in s.scalars(sa.select(BriefIssue).where(BriefIssue.id.in_(issue_ids))):
            issue_brief[str(iss.id)] = str(iss.brief_id)
    brief_ids = ids("brief") | {uuid.UUID(b) for b in issue_brief.values()}
    if brief_ids:
        for b in s.scalars(sa.select(Brief).where(Brief.id.in_(brief_ids))):
            name = b.name if b.name.lower().endswith("brief") else f"{b.name} brief"
            got[("brief", str(b.id))] = (name, f"/brief/{b.id}")
        for iid, bid in issue_brief.items():
            if ("brief", bid) in got:
                got[("brief_issue", iid)] = got[("brief", bid)]
    if want := ids("run"):
        for r in s.scalars(sa.select(Run).where(Run.id.in_(want))):
            got[("run", str(r.id))] = (f"#{r.number:04d}", f"/sweep?run={r.number}")
    if want := ids("ontology_version"):
        for v in s.scalars(sa.select(OntologyVersion).where(OntologyVersion.id.in_(want))):
            got[("ontology_version", str(v.id))] = (
                f"version {v.number}",
                "/ontology?panel=history",
            )
    for e in events:
        if e.object_type == "membership" or e.action.startswith("member."):
            got.setdefault((e.object_type, e.object_id), ("People", "/settings?tab=people"))
        elif e.action == "company.ai_cap_changed":
            got[(e.object_type, e.object_id)] = ("AI usage", "/usage")
        elif e.action.startswith("company.export"):
            got[(e.object_type, e.object_id)] = ("Your data", "/settings?tab=data")
        elif e.action.startswith("settings."):
            got[(e.object_type, e.object_id)] = ("Settings", "/settings")
    return got


def _parts(sentence: str, obj: tuple[str, str] | None) -> list[tuple[str, str | None]]:
    if "{obj}" in sentence:
        head, _, tail = sentence.partition("{obj}")
        label, href = obj if obj else ("", None)
        if not label:  # no screen: the sentence reads without it
            return [((head.rstrip() + tail).replace("  ", " ").strip(), None)]
        return [x for x in ((head, None), (label, href), (tail, None)) if x[0]]
    if obj:
        return [(sentence + " · ", None), obj]
    return [(sentence, None)]


# --- details -------------------------------------------------------------------------------------


# internal keys never shown: references, ids, hashes, fingerprints, keys, jobs, prompts
INTERNAL_KEY = re.compile(
    r"^(ref|refs|id|ids|.*_id|.*_ids|fingerprint|.*hash|key|.*_key|keys|job|prompt|spec|guard|"
    r"via|patch|origin|at|.*_at|range|record|records|suggestion|input|output)$",
    re.I,
)
# values shaped like a code ("alert:ontology_waiting", "metric:net_revenue"), an id or a hash
CODE_VALUE = re.compile(
    r"^[\w.-]+:[\w:./-]+$|^[0-9a-f]{8}-[0-9a-f]{4}-|^[0-9a-f]{12,}$|^\d{4}-\d\d-\d\dT", re.I
)
WORD_VALUE = re.compile(r"^[a-z]+(_[a-z]+)*$")  # a status word: "sent", "needs_confirmation"
LABELS = {
    "status": "Status",
    "state": "State",
    "use": "Used for",
    "rows": "Rows read",
    "tables": "Tables",
    "number": "Run",
    "set_by": "Set by",
    "data_lives": "Where the data lives",
    "chars": "Length",
}
VALUES = {
    "sent": "Sent",
    "done": "Finished",
    "failed": "Didn't finish",
    "bounced": "Couldn't be delivered",
    "needs_confirmation": "Waiting for you",
    "needs_source": "Needs a source",
    "confirmed": "Confirmed",
    "skipped": "Skipped",
    "metric_source": "Used for numbers",
    "dimension": "Used for breakdowns",
    "ignored": "Left out",
    "scanning": "Being looked through",
    "connected": "Connected",
    "draft": "Not connected yet",
    "open": "Open",
    "accepted": "Accepted",
    "declined": "Declined",
    "unchecked": "Not checked yet",
    "scan": "Othor AI's first look",
    "owner": "Super admin",
    "admin": "Admin",
    "model": "Othor AI",
    "template": "Plain wording",
}


def _plain_key(key: str) -> bool:
    return not INTERNAL_KEY.match(str(key))


def _say(v: Any) -> str:
    """A value in plain words; "" when there's nothing plain to show (a code, an id, a hash)."""
    if v is None or v == "" or v == [] or v == {}:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, int | float):
        return fmt.num(v, decimals=0 if float(v).is_integer() else 2)
    if isinstance(v, list):
        said = [x for x in (_say(y) for y in v) if x]
        shown = ", ".join(said[:6])
        return shown + (f" and {len(said) - 6} more" if len(said) > 6 else "")
    if isinstance(v, dict):
        parts = [(k, _say(x)) for k, x in v.items() if _plain_key(k)]
        return "; ".join(f"{_label(k)}: {x}" for k, x in parts if x)[:200]
    text = str(v).strip()
    if CODE_VALUE.search(text):
        return ""
    if text in VALUES:
        return VALUES[text]
    if WORD_VALUE.match(text) and "_" in text:
        text = text.replace("_", " ")
    return text if len(text) <= 160 else text[:157] + "…"


def _label(key: str) -> str:
    if key in LABELS:
        return LABELS[key]
    k = str(key).replace("_", " ").strip()
    return k[:1].upper() + k[1:]


def details(before: Any, after: Any) -> list[tuple[str, str, str]]:
    """Before and after in plain words: secret and internal keys dropped, codes and ids left
    out; empty when nothing plain is left (the row then has no Details)."""
    b = safe(before) if isinstance(before, Mapping) else {}
    a = safe(after) if isinstance(after, Mapping) else {}
    out = []
    for k in [*b, *(k for k in a if k not in b)]:
        if k in HIDDEN_KEYS or not _plain_key(k):
            continue
        sb, sa_ = _say(b.get(k)), _say(a.get(k))
        if sb or sa_:
            out.append((_label(k), sb, sa_))
    return out


def who(e: Event, ppl: People) -> str:
    """Who, in plain words; Othor AI's own drafting reads "Othor AI (asked by …)" even on rows
    that name only the person who asked (words.AI_MADE)."""
    a = actors.parse(e.actor)
    if e.action in words.AI_MADE and a.kind == actors.USER:
        uid = actors.person_id(a, ppl.by_email)
        a = actors.Actor(actors.OTHOR_AI, user_id=uid, raw=e.actor)
    return actors.label(a, ppl.names, by_email=ppl.by_email)


# --- rows ----------------------------------------------------------------------------------------


def _event_row(
    e: Event, ppl: People, objs: Mapping[tuple[str, str], tuple[str, str]], now: dt.datetime
) -> Row:
    a = actors.parse(e.actor)
    obj = objs.get((e.object_type, e.object_id))
    sent = words.sentence(e.action, e.before, e.after, obj[0] if obj else "")
    after = e.after if isinstance(e.after, Mapping) else {}
    note = ""
    if after.get("drafted_by") == actors.DRAFTED_BY_AI:
        note = (
            "Othor AI drafted this in chat; "
            + actors.label(a, ppl.names, by_email=ppl.by_email)
            + " applied it."
        )
    links: list[tuple[str, str]] = []
    if e.object_type == "run" and obj:
        links.append((f"Open run {obj[0]}", obj[1]))
    return Row(
        id=str(e.id),
        at=e.at,
        when=fmt.said_at(e.at, now),
        who=who(e, ppl),
        kind=words.KIND_LABEL.get(words.kind_of(e.action), ""),
        what=_parts(sent, obj),
        details=details(e.before, e.after),
        note=note,
        links=links,
    )


def _model_rows(
    s: Session, f: Filters, since: dt.datetime | None, cur: Any, limit: int, now: dt.datetime
) -> list[Row]:
    from othor.ai import usage
    from othor.ai.models import ModelCall

    if f.who not in ("", "ai") or f.kind not in ("", "ai"):
        return []
    q = sa.select(ModelCall)
    if since is not None:
        q = q.where(ModelCall.created_at >= since)
    if cur is not None:
        at, rid = cur
        q = q.where(
            sa.or_(
                ModelCall.created_at < at,
                sa.and_(ModelCall.created_at == at, ModelCall.id < rid),
            )
        )
    q = q.order_by(ModelCall.created_at.desc(), ModelCall.id.desc()).limit(limit)
    rows = []
    for m in s.scalars(q):
        task = usage.LABEL.get(m.prompt_id, m.prompt_id.replace("_", " "))
        result = model_result(m.guard_result)
        rows.append(
            Row(
                id=str(m.id),
                at=m.created_at,
                when=fmt.said_at(m.created_at, now),
                who="Othor AI",
                kind=words.KIND_LABEL["ai"],
                what=[(f"Othor AI wrote: {task} · {result}", None)],
            )
        )
    return rows


def model_result(guard: str) -> str:
    """What became of Othor AI's writing, in plain words."""
    if guard == "passed":
        return "kept"
    if guard.startswith("failed"):
        return "set aside by the numbers check"
    if guard == "refused":
        return "set aside"
    return "not written"


def _events_query(
    f: Filters, ppl: People, since: dt.datetime | None, cur: Any
) -> sa.Select[tuple[Event]]:
    q = sa.select(Event)
    for c in (_who_clause(f.who, ppl.by_email), _kind_clause(f.kind)):
        if c is not None:
            q = q.where(c)
    if f.kind != "people":  # a sign-in code request is shown under People & sign-in only
        q = q.where(Event.action.notin_(sorted(words.QUIET)))
    if since is not None:
        q = q.where(Event.at >= since)
    if cur is not None:
        at, rid = cur
        q = q.where(sa.or_(Event.at < at, sa.and_(Event.at == at, Event.id < rid)))
    return q.order_by(Event.at.desc(), Event.id.desc())


def page(
    s: Session, f: Filters, now: dt.datetime, after: str | None = None, size: int = PAGE
) -> Page:
    """One page of rows, newest first; `more` is the next page's cursor (None at the end)."""
    since = _since(f.days, _utc(now))
    cur = _cursor(after)
    ppl = people(s)
    events = list(s.scalars(_events_query(f, ppl, since, cur).limit(size + 1)))
    models = _model_rows(s, f, since, cur, size + 1, now)
    ppl = people(s, (e.actor for e in events))
    objs = _objects(s, events)
    rows = [_event_row(e, ppl, objs, now) for e in events] + models
    rows.sort(key=lambda r: (_utc(r.at), uuid.UUID(r.id)), reverse=True)
    more = None
    if len(rows) > size:
        rows = rows[:size]
        last = rows[-1]
        more = f"{_utc(last.at).isoformat()}|{last.id}"
    return Page(rows, more)


def every_row(s: Session, f: Filters, now: dt.datetime) -> list[Row]:
    """The filtered rows for the CSV (up to CSV_LIMIT), in the page's order."""
    out: list[Row] = []
    cursor: str | None = None
    while len(out) < CSV_LIMIT:
        p = page(s, f, now, cursor, size=500)
        out += p.rows
        if p.more is None:
            break
        cursor = p.more
    return out[:CSV_LIMIT]


def who_what(s: Session, events: list[Event]) -> list[tuple[str, str]]:
    """The plain who and what of each event (the export's events.csv, 0.35)."""
    ppl = people(s, (e.actor for e in events))
    objs = _objects(s, events)
    out = []
    for e in events:
        obj = objs.get((e.object_type, e.object_id))
        parts = _parts(words.sentence(e.action, e.before, e.after, obj[0] if obj else ""), obj)
        out.append(
            (
                who(e, ppl),
                "".join(t for t, _ in parts),
            )
        )
    return out
