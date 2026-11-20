# SPDX-License-Identifier: Apache-2.0
"""The brief's words from computed values (docs/04 §9, 4h1). Template first: the brief is complete
without a model. Each clause is written only when the data makes it true.

- **Title:** from the top item. Net hiding gross: "South fell ₹1.4 Cr while West’s growth hid
  it", then, from its customer drill-down, "three accounts carry 70% of the drop" and "none has
  invoiced since 9 Oct" (the latest of their last invoices, only when each has been silent for
  two weeks or more by the end of the window). Under 70%
  explained, it says the rest is not determinable (4h1 B). Other detectors: the finding title.
- **Checks (4h1 D, E)**, run before the brief is made: a title has a number and a position
  (names a breakdown value or an account); Ask today is one question, for one named person.
- **Line:** which headline numbers are on plan (in band, on track, no change).
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any

from othor.core import fmt
from othor.engine.sweep_text import money

EXPLAINED = Decimal("0.70")
SILENT_DAYS = 14
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
ON_PLAN = ("in band", "on track", "no change")


def _count(n: int, noun: str) -> str:
    return f"{WORDS.get(n, str(n))} {noun}{'' if n == 1 else 's'}"


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _drill_clauses(d: dict[str, Any]) -> str:
    accounts = d.get("accounts") or []
    if not accounts:
        return ""
    share = Decimal(str(d["share"]))
    out = f"{_count(len(accounts), 'account')} carry {fmt.pct(share)} of the drop"
    if share < EXPLAINED:
        return out + "; the rest is not determinable from current sources"
    # "none has invoiced since <the latest of their last invoices>", only when every one of them
    # has been silent for at least two weeks by the end of the window (else it says nothing)
    lasts = [a.get("last") for a in accounts]
    if all(lasts) and d.get("to"):
        latest = max(dt.date.fromisoformat(str(x)) for x in lasts)
        if (dt.date.fromisoformat(str(d["to"])) - latest).days >= SILENT_DAYS:
            out += f" and none has invoiced since {fmt.day_month(latest)}"
    return out


TITLE_MAX = 170  # longer than this, a known pause goes first in Situation instead


def paused_note(d: dict[str, Any]) -> str:
    """'one is Skarfab’s known pause to 1 Dec' (decided 6 Nov 2026)."""
    ps = [a for a in d.get("accounts") or [] if a.get("paused")]
    if not ps:
        return ""
    names = _and([f"{a['name']}’s" for a in ps])
    if len(ps) == 1:
        until = fmt.day_month(dt.date.fromisoformat(ps[0]["paused"]["until"]))
        return f"one is {names} known pause to {until}"
    return f"{WORDS.get(len(ps), str(len(ps)))} are {names} known pauses"


def pause_sentence(d: dict[str, Any]) -> str:
    """For Situation and the finding's line: 'Skarfab is on a known pause to 1 Dec (your
    answer, 30 Oct).'"""
    out = []
    for a in d.get("accounts") or []:
        p = a.get("paused")
        if not p:
            continue
        until = fmt.day_month(dt.date.fromisoformat(p["until"]))
        said = f"{a['name']} is on a known pause to {until}"
        if p.get("answered_on"):
            said += f" (your answer, {fmt.day_month(dt.date.fromisoformat(p['answered_on']))})"
        out.append(said + ".")
    return " ".join(out)


def headline(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    top = items[0]
    sl = top.get("slots") or {}
    if top["detector"] == "net_hiding_gross" and sl.get("fall") and sl.get("rise"):
        fall, rise = sl["fall"], sl["rise"]
        head = (
            f"{fall['name']} fell {money(abs(Decimal(str(fall['change']))))} "
            f"while {rise['name']}’s growth hid it"
        )
        d = sl.get("drill") or {}
        tail = _drill_clauses(d)
        note = paused_note(d)
        if tail and note and len(f"{head}; {tail}; {note}.") <= TITLE_MAX:
            return f"{head}; {tail}; {note}."
        return f"{head}; {tail}." if tail else f"{head}."
    t = str((top.get("text") or {}).get("title") or "")
    return t if t.endswith(".") else t + "."


# What changed leads the title (owner, 16 Nov 2026), as it leads the email subject (M15.7d): the
# same top finding had titled every brief for five days
CHANGED = ("New", "Worse", "Came back")


def lead_headline(ranked: list[dict[str, Any]]) -> str:
    """The highest-ranked finding that changed tonight (New, Worse, Came back) titles the brief;
    when none did, the top one does, with how long it has been open."""
    if not ranked:
        return ""
    lead = next((f for f in ranked if str(f.get("change") or "").startswith(CHANGED)), None)
    if lead is not None:
        return headline([lead])
    days = int(ranked[0].get("days_open") or 1)
    return f"Still open, day {fmt.num(days)}: {headline(ranked)}"


def check_title(t: str, items: list[dict[str, Any]]) -> list[str]:
    """4h1 D: a label, or a number with no position, fails."""
    if not re.search(r"[₹$%]|\d+(\.\d+)?\s*(Cr|L|K|M|B|d|days)\b", t):  # ₹ or $ (M15.6)
        return ["label"]
    named = set()
    for f in items:
        sl = f.get("slots") or {}
        named |= {str(x) for x in f.get("entities") or []}
        for k in ("fall", "rise"):
            if sl.get(k):
                named.add(str(sl[k]["name"]))
        named |= {a["name"] for a in (sl.get("drill") or {}).get("accounts", [])}
        if f.get("breakdown_value"):
            named.add(str(f["breakdown_value"]))
    if not any(n and n in t for n in named):
        return ["no position"]
    return []


def check_ask(a: dict[str, Any]) -> list[str]:
    """4h1 E: one question, answerable today, for one named person."""
    if not a.get("to"):
        return ["no owner"]
    q = a.get("text", "").count("?")
    if q == 0:
        return ["not a question"]
    if q > 1:
        return ["more than one"]
    return []


def _owner_of(owners: list[dict[str, Any]], area: str) -> dict[str, Any] | None:
    return next((o for o in owners if area in (o.get("areas") or [])), None)


# 0.28.4 (owner, 19 Nov 2026): whom to ask, even before a team is set up: a plain role from the
# number's area (and where it happened), worked out by code
ROLE_OF_AREA = {
    "Sales": "your sales head",
    "Collections": "whoever runs collections",
    "Operations": "your operations head",
    "Finance": "your finance head",
    "Customers": "your account managers",
}
ROLE_OF_DETECTOR = {
    "missing_records": "your account managers",
    "concentration": "your sales head",
    "cross_source": "whoever keeps your books",
}


def role_for(item: dict[str, Any], spec: dict[str, Any] | None) -> str:
    """'your sales head for South'; 'whoever looks after dispatch time' when nothing better."""
    m = ((spec or {}).get("metrics") or {}).get(str(item.get("metric") or "")) or {}
    role = ROLE_OF_AREA.get(str(m.get("area") or "")) or ROLE_OF_DETECTOR.get(
        str(item.get("detector") or "")
    )
    if not role:
        name = str(m.get("name") or "").strip()
        role = f"whoever looks after {name[:1].lower() + name[1:]}" if name else ""
    where = str(item.get("breakdown_value") or "").strip()
    return f"{role} for {where}" if role and where else role


def ask_today(
    items: list[dict[str, Any]],
    owners: list[dict[str, Any]],
    spec: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    got = _ask_today(items, owners)
    if got is not None and not got.get("to") and not got.get("role"):
        got = {**got, "role": role_for(items[0], spec)}
    return got


def _ask_today(items: list[dict[str, Any]], owners: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    top = items[0]
    drill = (top.get("slots") or {}).get("drill") or {}
    # only the accounts with no explanation: never one under a known pause (6 Nov 2026)
    accounts = [a["name"] for a in drill.get("accounts") or [] if not a.get("paused")]
    if accounts and Decimal(str(drill.get("share", 0))) >= EXPLAINED:
        who = _owner_of(owners, "customer")
        if who is not None:
            return {
                "text": f"Have {_and(accounts)} moved orders elsewhere, or are they on hold?",
                "to": who["name"],
                "role": who.get("role", ""),
            }
    q = (top.get("text") or {}).get("question") or {}
    to = (q.get("to") or [{}])[0]
    if not q.get("text"):
        return None
    return {"text": q["text"], "to": to.get("name"), "role": to.get("role", "")}


def _on_plan(tiles: list[dict[str, Any]], names: dict[str, str]) -> list[str]:
    return [
        names.get(t["metric"], t["label"])
        for t in tiles
        if any(t["comparison"]["text"].startswith(w) for w in ON_PLAN) and not t.get("stale")
    ]


def _sentence(names: list[str]) -> list[str]:
    """The first name as written; the rest lower case unless an acronym (DSO)."""
    return [
        n if i == 0 or n.split()[0].isupper() else n[:1].lower() + n[1:]
        for i, n in enumerate(names)
    ]


def line(tiles: list[dict[str, Any]], names: dict[str, str]) -> str:
    ok = _sentence(_on_plan(tiles, names))
    if not ok:
        return ""
    return f"{_and(ok)} {'is' if len(ok) == 1 else 'are'} on plan."


def quiet_title(tiles: list[dict[str, Any]], names: dict[str, str]) -> str:
    ok = _sentence(_on_plan(tiles, names))
    if ok and len(ok) == len(tiles):
        return f"{_and(ok)} {'is' if len(ok) == 1 else 'are'} on plan; nothing needs you today."
    return "Nothing needs you today."


def drill_sentence(d: dict[str, Any]) -> str:
    """The same clauses as a finding's line (10h): "Three accounts carry 72% of the drop. None
    has invoiced since 9 Oct." Each sentence only when the data makes it true."""
    c = _drill_clauses(d)
    if not c:
        return ""
    c = c.replace(" and none has invoiced since ", ". None has invoiced since ")
    c = c.replace("; the rest is not determinable", ". The rest is not determinable")
    pause = pause_sentence(d)
    return c[:1].upper() + c[1:] + "." + (f" {pause}" if pause else "")
