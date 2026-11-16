# SPDX-License-Identifier: Apache-2.0
"""Connect a source from Metrics (M4b: 2nca, 2ncb; also 6h after setup).

The connector panel opens with `for=metric:<key>`; the source remembers it. When the source is
connected, a draft job (mode "source") redrafts that metric from the new tables, adds the
drafts the source unlocks, and computes today's value for each by SQL. Rules only here: a
connected source's tables are matched by code (candidates), never by a model's numbers.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.connectors import service
from othor.connectors.models import Source
from othor.jobs.queue import enqueue
from othor.ontology.compiler.catalog import Catalog
from othor.setup import drafts
from othor.setup.candidates import table_candidates
from othor.setup.models import SetupDraft
from othor.setup.patches import Patch, PatchRefused, apply_patch
from othor.setup.rules_draft import rule_drafts, tokens
from othor.setup.suggest_sources import KINDS, Suggestion, suggest, without_connected_kinds

FOR = "for"


def mark_for(src: Source, key: str) -> None:
    src.config = {**(src.config or {}), FOR: f"metric:{key}"}


def for_key(src: Source | None) -> str | None:
    tag = str(((src.config if src else None) or {}).get(FOR) or "")
    return tag.split(":", 1)[1] if tag.startswith("metric:") else None


def suggested(s: Session, keep: Source | None = None) -> list[Suggestion]:
    """Sources the owner named or their files point to, not connected yet (10a, 6c, 2n).
    `keep`: the source being connected now; its suggestion stays, so the panel keeps the name the
    flow started with ("Connect the Order tracker", 2nca–6h, M12b)."""
    from othor.setup import business
    from othor.setup.inputs import gather

    inp = gather(s)
    msg = business.message(s)
    sources = list(s.scalars(sa.select(Source).where(Source.deleted_at.is_(None))))
    names = {x.name for x in sources if keep is None or x.id != keep.id}
    # a source is connected under its own name ("Order tracker FY27" for "Order tracker")
    have = [
        _plain(
            " ".join(
                [x.name, *(str(f.get("title") or "") for f in (x.config or {}).get("files", []))]
            )
        )
        for x in sources
        if keep is None or x.id != keep.id
    ]
    kinds = {x.kind for x in sources if keep is None or x.id != keep.id}
    return without_connected_kinds(
        [
            sug
            for sug in suggest(msg.data_lives or [], msg.text or "", inp.refs, names)
            if not any(_plain(sug.name or sug.term) <= h for h in have if h)
        ],
        kinds,
    )


_STOP = {"the", "and", "for", "sheet", "sheets", "source", "data", "file", "google", "home"}


def _plain(text: str) -> set[str]:
    """Plain words (a name in brackets counts: "Open orders (Order tracker sheet)")."""
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def suggestion_for(row: SetupDraft, suggestions: list[Suggestion]) -> Suggestion | None:
    """The suggested source a waiting metric names (its name or `needs`), else none."""
    words = _plain(_words(row))

    def score(x: Suggestion) -> int:
        return len(words & _plain(" ".join([x.term, x.name or "", *x.files])))

    best = max(suggestions, key=score, default=None)
    return best if best is not None and score(best) else None


def connect_label(sug: Suggestion) -> str:
    return f"Connect the {sug.name}" if sug.name else f"Connect {KINDS[sug.kind]}"


def connected(s: Session, src: Source, *, actor: str) -> None:
    """The source is connected: read it now (Sheets), then redraft for the metric."""
    if src.kind == "gsheets":
        service.read_now(s, src, actor=actor)
    enqueue(
        s,
        "draft",
        {"mode": "source", "source_id": str(src.id), "for": for_key(src), "actor": actor},
    )


def _sub(cat: Catalog, source: str) -> Catalog:
    return Catalog(
        tables={k: t for k, t in cat.tables.items() if k[0] == source},
        sources={n: v for n, v in cat.sources.items() if n == source},
    )


def _words(row: SetupDraft) -> str:
    b = row.body
    return " ".join(
        str(x) for x in (b.get("name"), b.get("description"), b.get("needs"), row.note) if x
    )


@dataclass
class SourceDrafts:
    redrafted: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # (key, body)
    added: list[drafts.Draft] = field(default_factory=list)

    @property
    def target(self) -> tuple[str, dict[str, Any]] | None:
        return self.redrafted[0] if self.redrafted else None


def waiting(row: SetupDraft) -> bool:
    """A metric waiting on a source: redrafted when one is connected, even after Confirm."""
    return row.state != "skipped" and row.body.get("status") == "needs_source"


def draft_for_source(
    cat: Catalog,
    source: str,
    source_id: uuid.UUID,
    rows: dict[str, SetupDraft],
    key: str | None,
    *,
    rules: list[tuple[str, dict[str, Any]]],
    vocabulary: list[dict[str, Any]],
    names: str = "",
) -> SourceDrafts:
    """The metric it was connected for, the other metrics waiting on this source (their `needs`
    names it), and new drafts from its tables."""
    sub = _sub(cat, source)
    out = SourceDrafts()
    named = tokens(f"{source} {names}")
    for r in sorted(rows.values(), key=lambda x: (x.key != key, x.position, x.key)):
        is_target = r.key == key and (not drafts.locked(r) or waiting(r))
        if not is_target and not (waiting(r) and tokens(_words(r)) & named):
            continue
        got = _redraft(r, sub)
        if got is not None:
            out.redrafted.append(got)
    taken = set(rows) | ({key} if key else set())
    used = {(b.get("table") or {}).get("table") for _, b in out.redrafted}
    for d in rule_drafts(sub, [], rules=rules, vocabulary=vocabulary, sheets=True):
        if d.key in taken or (d.body.get("table") or {}).get("table") in used:
            continue
        d.origin = {**d.origin, "source_id": str(source_id)}
        out.added.append(drafts.Draft.from_rule(d))
    return out


_NOT_A_SUM = re.compile(
    r"\b(rate|ratio|share|average|avg|per|time|days|cycle|margin|quality|score|health|mix|%)\b",
    re.I,
)


def _redraft(row: SetupDraft, sub: Catalog) -> tuple[str, dict[str, Any]] | None:
    """A waiting metric that is a sum of one column (Order book value) is redrafted by code. A
    rate, ratio or duration needs more than one column: it stays waiting for the owner's chat."""
    if row.body.get("unit") in ("percent", "ratio", "days") or _NOT_A_SUM.search(
        str(row.body.get("name") or row.key)
    ):
        return None
    snapshot = row.body.get("kind") == "snapshot"
    for tc in table_candidates(sub, _words(row)):
        amount = next((u for u in tc.useful if "value" in u or "amount" in u), None)
        if amount is None:
            continue
        from othor.setup.chat import _first_date

        patch = Patch(
            "use_column",
            {
                "table": {
                    "source": tc.table.source_name,
                    "schema": tc.table.schema,
                    "table": tc.table.name,
                },
                "column": amount,
                **({} if snapshot else {"date": _first_date(tc.table)}),
            },
        )
        base = {k: v for k, v in row.body.items() if k not in ("needs",)}
        base.setdefault("kind", "snapshot" if snapshot else "flow")
        try:
            got = apply_patch(base, patch, sub, {})
        except PatchRefused:
            continue
        body = dict(got.body)
        if not body.get("description"):
            by = f" by {body['date']}" if body.get("date") else ""
            body["description"] = f"Sum of {tc.table.name}.{amount}{by}"
        return row.key, body
    return None


@dataclass
class Review:
    source: Source
    target: SetupDraft | None
    added: list[SetupDraft]  # everything else this source drafted (waiting first, then new)
    drafting: bool

    @property
    def rows(self) -> list[SetupDraft]:
        return ([self.target] if self.target is not None else []) + self.added

    @property
    def waiting(self) -> int:
        return sum(1 for r in self.rows if (r.origin or {}).get("was_waiting"))


def review(s: Session, source_id: uuid.UUID) -> Review:
    src = s.scalars(sa.select(Source).where(Source.id == source_id)).one()
    key = for_key(src)
    rows = drafts.stored(s)
    target = next((r for r in rows if r.key == key), None) if key else None
    added = [
        r for r in rows if (r.origin or {}).get("source_id") == str(source_id) and r.key != key
    ]
    added.sort(key=lambda r: (not (r.origin or {}).get("was_waiting"), r.position))
    from othor.jobs.models import Job

    pending = s.scalars(
        sa.select(Job).where(Job.kind == "draft", Job.status.in_(("queued", "running")))
    ).first()
    return Review(src, target, added, pending is not None)


@dataclass
class Waiting:
    """A suggested source with the metrics waiting on it (6c Suggested, 6e)."""

    suggestion: Suggestion
    metrics: list[str]

    @property
    def title(self) -> str:
        return self.suggestion.title

    @property
    def tile(self) -> str:
        return {"postgres": "PG", "mysql": "MY", "gsheets": "GS"}[self.suggestion.kind]

    @property
    def href(self) -> str:
        k = self.suggestion.kind
        return "/sources?panel=sheets" if k == "gsheets" else f"/sources?panel=connect&kind={k}"

    @property
    def names(self) -> str:
        m = self.metrics
        return m[0] if len(m) == 1 else ", ".join(m[:-1]) + " and " + m[-1]


def waiting_on_sources(s: Session) -> list[Waiting]:
    """Suggested sources that metrics are waiting on, most metrics first."""
    rows = [r for r in drafts.stored(s) if waiting(r)]
    if not rows:
        return []
    sugg = suggested(s)
    out: dict[str, Waiting] = {}
    for r in rows:
        sug = suggestion_for(r, sugg)
        if sug is None:
            continue
        w = out.setdefault((sug.name or sug.term).lower(), Waiting(sug, []))
        w.metrics.append(r.body.get("name") or r.key)
    return sorted(out.values(), key=lambda w: (-len(w.metrics), w.title))
