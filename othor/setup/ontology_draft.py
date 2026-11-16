# SPDX-License-Identifier: Apache-2.0
"""The Ontology draft (M4b: 2n0): what Confirm will write as Ontology v1, read-only.

Four sections: the business (the owner's message; goals quoted from their files with the citation,
shown only, goal records come in M7), the numbers (the drafts and what needs the owner), names and
words (breakdowns, vocabulary, dimension maps), and known patterns. Every line is the owner's own
words, a file's quote read by code, or a draft's definition; no figure is written by a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.setup import confirm, drafts
from othor.setup.inputs import gather
from othor.setup.models import BusinessMessage

SHOWN_NUMBERS = 3


@dataclass
class Line:
    text: str
    lead: str | None = None  # a bold name before the text
    cite: str | None = None  # where it came from
    flag: str | None = None  # a "needs you" line under it
    dot: str = "ok"  # ok found · warn to check · neg conflict


@dataclass
class Section:
    title: str
    count: str
    lines: list[Line] = field(default_factory=list)
    hint: str | None = None


def _cite(upload: str, locator: str) -> str:
    stem = upload.rsplit(".", 1)[0]
    return " · ".join(x for x in (stem, locator) if x)


def build(s: Session) -> dict[str, Any]:
    inp = gather(s)
    rows = [r for r in drafts.stored(s) if r.state != "skipped"]
    c = confirm.counts(rows)
    msg = s.scalars(sa.select(BusinessMessage).order_by(BusinessMessage.version.desc())).first()

    business = Section("The business", "")
    if msg is not None and msg.text.strip():
        business.lines.append(Line(msg.text.strip(), cite="Your message"))
    goals = [(u, ref) for u, ref in inp.refs if ref.kind == "target"]
    for u, ref in goals[:3]:
        business.lines.append(Line(ref.text, lead="Goal", cite=_cite(u, ref.locator)))
    business.count = f"{len(business.lines)} line{'s' if len(business.lines) != 1 else ''}"

    numbers = Section("Numbers", "")
    needs = [r for r in rows if r.confidence in ("conflict", "check", "low") and not r.frozen]
    for r in sorted(needs, key=lambda x: (x.confidence != "conflict", x.position))[:SHOWN_NUMBERS]:
        b = r.body
        flag = r.note or ("Conflict to settle." if r.confidence == "conflict" else "To check.")
        if r.state == "needs_source":
            flag = "Needs a source."
        cites = [_cite(x.get("upload", ""), "") for x in (r.refs or []) if x.get("upload")]
        t = (b.get("table") or {}).get("source")
        numbers.lines.append(
            Line(
                b.get("description") or "",
                lead=b.get("name") or r.key,
                cite=" · ".join(dict.fromkeys([*cites, *([t] if t else [])])) or None,
                flag=flag,
                dot="neg" if r.confidence == "conflict" else "warn",
            )
        )
    shown = {ln.lead for ln in numbers.lines}
    rest = [r.body.get("name") or r.key for r in rows if (r.body.get("name") or r.key) not in shown]
    if rest:
        more = f" and {len(rest) - 3} more" if len(rest) > 3 else ""
        sources = sorted({(r.body.get("table") or {}).get("source", "") for r in rows} - {""})
        numbers.lines.append(
            Line(", ".join(rest[:3]) + more + ".", cite=" · ".join(sources) or None)
        )
    numbers.count = f"{len(rows)} · {len(needs)} need you" if needs else f"{len(rows)}"

    words = Section("Names and words", "")
    for key, b in sorted(inp.breakdowns.items()):
        words.lines.append(
            Line(f"split by {b.get('column')}", lead=b.get("name", key), cite=b.get("table"))
        )
    for row in drafts.stored(s, "vocabulary"):
        words.lines.append(Line(row.body.get("text") or row.body.get("term", ""), cite=_refs(row)))
    for d in drafts.stored(s, "discovery"):
        if d.state != "skipped" and d.body.get("type") == "dimension":
            words.lines.append(Line(d.body["text"], cite="Found in your data"))
    words.count = f"{len(words.lines)} line{'s' if len(words.lines) != 1 else ''}"

    patterns = Section("Known patterns", "", hint="Sweep skips these")
    for d in drafts.stored(s, "discovery"):
        if d.state != "skipped" and d.body.get("type") == "pattern":
            patterns.lines.append(Line(d.body["text"], cite="Found in your data"))
    for row in drafts.stored(s, "known_pattern"):
        patterns.lines.append(Line(row.body.get("name", row.key), cite=_refs(row)))
    patterns.count = f"{len(patterns.lines)} line{'s' if len(patterns.lines) != 1 else ''}"

    files = len(inp.uploads)
    sources = sorted(inp.cat.sources)
    built = [f"{files} file{'s' if files != 1 else ''}"] if files else []
    if msg is not None and msg.text.strip():
        built.append("your message")
    built += sources
    return {
        "needs": c["conflict"] + c["check"],
        "conflict": c["conflict"],
        "check": c["check"],
        "sections": [business, numbers, words, patterns],
        "built": "Built from " + " · ".join(built) if built else None,
    }


def _refs(row: Any) -> str | None:
    refs = row.refs or []
    return _cite(refs[0].get("upload", ""), refs[0].get("locator", "")) if refs else None
