# SPDX-License-Identifier: Apache-2.0
"""Cross-source matching (6h, answered and refined 5 Nov 2026): how many of another source's keys
match an entity, measured on the nightly read. Pure: from the run's probe files only, so replay
reproduces it.

- By identity (GSTIN) when the other source has the entity's identity column; else by name.
- Names are normalised hard: case, spaces, punctuation, and company suffixes (Pvt Ltd, Private
  Limited, Ltd, Limited, LLP, & Co, Industries/Inds). A name that fits two customers is
  ambiguous: never matched, listed for the owner.
- A tab is proposed for a join when at least 80% of its keys match and at least 20 matched.
  A name match never joins on raw names: it proposes alias records (their name → the entity's
  name) and a join through those aliases, graded lower than a match by identity.
- Unmatched keys are listed by value (the key column is never personal or excluded).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

EXAMPLES = 10
PROPOSE_AT = Decimal("0.8")
MIN_MATCHED = 20
SUFFIXES = (
    "private limited",
    "pvt ltd",
    "pvt",
    "limited",
    "ltd",
    "llp",
    "and co",
    "co",
    "industries",
    "inds",
)
_PUNCT = re.compile(r"[^\w\s]")


def norm_identity(v: str) -> str:
    return re.sub(r"[\s\-./]", "", str(v)).upper()


def norm_name(v: str) -> str:
    s = str(v).casefold().replace("&", " and ")
    s = " ".join(_PUNCT.sub(" ", s).split())
    changed = True
    while changed and s:
        changed = False
        for suffix in SUFFIXES:
            if s == suffix:
                break
            if s.endswith(" " + suffix):
                s = s[: -len(suffix) - 1].strip()
                changed = True
                break
    return s


def compute(spec: dict[str, Any], files: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    probes = spec.get("probes") or {}
    entities = probes.get("entities") or {}
    out = []
    for m in probes.get("matches") or []:
        got = next(
            (x for x in (files.get(m["source"]) or {}).get("matches", []) if x["key"] == m["key"]),
            None,
        )
        e = entities.get(m["entity"])
        rows = (
            ((files.get(e["source"]) or {}).get("entities") or {}).get(m["entity"]) if e else None
        )
        if got is None or rows is None:
            continue
        identity = m["by"] == "identity"
        norm = norm_identity if identity else norm_name
        col = 2 if identity else 0  # entity rows: name, status, identity, created
        index: dict[str, set[str]] = {}
        for r in rows:
            if r[col]:
                index.setdefault(norm(r[col]), set()).add(str(r[0]))
        vals = sorted(set(got["values"]))
        pairs, ambiguous, miss = [], [], []
        for v in vals:
            names = sorted(index.get(norm(v), set()))
            if len(names) == 1:
                pairs.append([v, names[0]])
            elif names:
                ambiguous.append([v, names])
            else:
                miss.append(v)
        share = Decimal(len(pairs)) / Decimal(len(vals)) if vals else Decimal(0)
        out.append(
            {
                "key": m["key"],
                "entity": m["entity"],
                "by": m["by"],
                "grade": "identity" if identity else "name",
                "source": m["source"],
                "table": m["table_name"],
                "column": m["column"],
                "to_source": m["to_source"],
                "to_table": m["to_name"],
                "to_column": m["to_column"],
                "total": len(vals),
                "matched": len(pairs),
                "share": str(share.quantize(Decimal("0.0001"))),
                "unmatched": len(miss),
                "examples": miss[:EXAMPLES],
                "ambiguous": ambiguous[:EXAMPLES],
                "ambiguous_count": len(ambiguous),
                "pairs": pairs,
                "joined": bool(m.get("joined")),
                "propose": not m.get("joined")
                and len(pairs) >= MIN_MATCHED
                and share >= PROPOSE_AT,
            }
        )
    return out
