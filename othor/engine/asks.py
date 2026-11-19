# SPDX-License-Identifier: Apache-2.0
"""Home questions from findings (docs/04 §10): a Needs confirmation finding asks which system is
right; a Not determinable one asks what happened, listing the reasons its checks didn't rule out.
Addressed to the owners of the finding's area (Ontology `owner` records). Text is a template
filled with computed values only; each question is fingerprinted by its finding (never repeated).
"""

from __future__ import annotations

from typing import Any

from othor.core import fmt


def owners_for(spec: dict[str, Any], f: dict[str, Any]) -> list[str]:
    keys = {f.get("metric"), f.get("family"), f.get("breakdown_value"), f.get("detector")}
    keys |= {e for e in f.get("entities", [])}
    if f.get("family", "").startswith("reconcile:"):
        keys.add(f["family"].split(":", 1)[1])
    names = [o["name"] for o in spec.get("owners", []) if keys & set(o.get("areas", []))]
    return sorted(set(names))


def ask(spec: dict[str, Any], f: dict[str, Any]) -> dict[str, Any] | None:
    if f.get("status") != "open":
        return None
    slots = f.get("slots", {})
    who = owners_for(spec, f)
    kind = f["state"]
    sides: list[str] = []
    replies: list[str] = []
    if f["state"] == "needs_confirmation":
        if "count" in slots:
            sides = [x for x in (slots.get("left_source"), slots.get("right_source")) if x]
            noun = slots.get("noun") or "records"
            text = f"Which system is right about these {fmt.num(slots['count'])} {noun}?"
            # the finding's own words (as on the Sweep card, 5c), not table names (3aa, M12b)
            from othor.engine.sweep_text import _singular

            lt, _, ls = str(slots.get("left", "")).partition(" in ")
            rtab, _, rs = str(slots.get("right", "")).partition(" in ")
            lsrc = slots.get("left_source") or ls or lt
            rsrc = slots.get("right_source") or rs or rtab
            rt = _singular(slots.get("right_table") or rtab or "match")
            why = (
                f"{fmt.num(slots['count'])} {noun} in {lsrc} have no matching {rt} in {rsrc} "
                f"after {slots.get('lag_days')} days."
            )
        else:
            sides = [x for x in (slots.get("a"), slots.get("b")) if x]
            text = f"Which figure is right: {slots.get('a', '')} or {slots.get('b', '')}?"
            grain = slots.get("grain") or "month"
            why = (
                f"They differ by {fmt.inr(f['money'])} for the last complete {grain} "
                f"(from {slots.get('period', '')})."
            )
        replies = [*(f"{x} is right" for x in sides), "Not sure, look into it"]
    elif f["state"] == "not_determinable":
        open_ = [r["text"] for r in f.get("reasons", []) if not r.get("ruled_out")]
        if not open_:
            return None
        name = slots.get("name") or (f.get("entities") or [""])[0]
        text = f"What happened with {name}?"
        why = (
            f"No invoice in {fmt.num(slots.get('gap_days', 0))} days, against every "
            f"{fmt.num(slots.get('median_days', 0))} days before. Possible: {', '.join(open_)}."
        )
        if f["detector"] == "missing_records" and f.get("entities"):
            replies = ["Yes, expected", "No, look into it"]
        else:
            replies = ["No, look into it"]
    else:
        return None
    money = str(f.get("money") or "0")
    return {
        "fingerprint": f"finding:{f['fingerprint']}",
        "text": text,
        "why": why,
        "unblocks": {"finding": f["fingerprint"], "detector": f["detector"]},
        "origin": {"rule": "finding", "finding": f["fingerprint"], "owners": who},
        "quick_replies": replies,
        "kind": kind,
        "subject": {
            "finding": f["fingerprint"],
            "detector": f["detector"],
            "metric": f.get("metric"),
            "entity": (f.get("entities") or [None])[0],
            "since": slots.get("last") or (f.get("window") or [None])[0],
            "sides": sides,
            "money": money,
        },
        "rank": rank_key(kind, money),
    }


RANK = {
    "needs_confirmation": "1",
    "not_determinable": "2",
    "pattern": "3",
    "needs": "4",
    "unmapped_plant": "5",
    "goal_missing": "3",  # 0.28: "What are you aiming for this year?"
}


def rank_key(kind: str, money: str | None = None) -> str:
    """The order on Home: the kind's group, then the larger amount first."""
    from decimal import Decimal

    m = Decimal(money or "0")
    inv = (Decimal("1e15") - min(max(m, Decimal(0)), Decimal("1e15"))).quantize(Decimal("0.01"))
    return f"{RANK.get(kind, '9')}|{inv:019.2f}"
