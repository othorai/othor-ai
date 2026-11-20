# SPDX-License-Identifier: Apache-2.0
"""Big Picture (v0.19, M19.5): a short, plain report of last night, written by Othor AI from the
run's computed results. Pure: facts in, the model's input out; the model's output in, the checked
report out. The model call and the store are the caller's (othor.web.big_picture_view).

Rule 1, AI drafts, code computes (owner, 15 Nov 2026: "free writing, numbers checked"):
- The model sees the computed text with its figures ("−₹0.19 Cr", "72%", "9 Oct", "FY27") and
  writes freely in plain words, copying figures exactly as given.
- The check. Every numeric span in a sentence it writes must be one of those computed values
  (a signed value also matches without its sign; a range's ends match on their own). Each becomes
  a link to where it came from. A sentence with any other numeral (any script: `\\d` matches
  Devanagari digits too) is dropped, never repaired.

The layout (owner's design, 15 Nov 2026): the summary; the headline numbers as tiles; one card per
finding with its own chart; the money at stake as tiles; up to 3 actions; the forecasts as cards
with a range bar; a glossary. The tiles, the stakes' figures, the charts and the forecast cards are
computed by code; the model writes the words around them, and its figures are checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# the figure check lives in othor.briefs.figures (0.29); these names stay for older imports
from othor.briefs.figures import NUM, Values
from othor.briefs.figures import norm as _norm  # noqa: F401
from othor.briefs.figures import sentence as _sentence

PROMPT, VERSION = "big_picture", "v1"
LANGUAGES = {"en": "English", "hi": "Hindi"}
CHARTS = 3  # finding cards, each with its chart
KPIS = 4  # the headline numbers in the tile row
STAKES = 4
FORECASTS = 2

HEADINGS = {
    "en": {
        "one_line": "Summary by Othor AI",
        "happening": "What’s happening",
        "why_it_matters": "Why it matters",
        "look_first": "What to look at first",
        "coming": "What’s coming",
        "words": "Words used",
        "priority": "In priority order",
        "expected": "expected",
        "open": "Open",
    },
    "hi": {
        "one_line": "Othor AI का सारांश",
        "happening": "क्या हो रहा है",
        "why_it_matters": "यह क्यों मायने रखता है",
        "look_first": "पहले क्या देखें",
        "coming": "आगे क्या है",
        "words": "इस्तेमाल हुए शब्द",
        "priority": "प्राथमिकता के क्रम में",
        "expected": "अनुमानित",
        "open": "खोलें",
    },
}
HEADER = {
    "en": "Written by Othor AI from last night’s computed results · every number is computed, "
    "not written by AI",
    "hi": "Othor AI ने कल रात के गणना किए गए नतीजों से लिखा · हर संख्या गणना से आई है, AI ने नहीं लिखी",
}


def _fact(
    vals: Values, fid: str, kind: str, text: str, href: str = "", **more: str
) -> dict[str, Any]:
    out: dict[str, Any] = {"id": fid, "kind": kind, "text": vals.note(text, href)}
    for k, v in more.items():
        if v:
            out[k] = vals.note(v, href)
    return out


@dataclass(frozen=True)
class Facts:
    inputs: dict[str, Any]  # the model's input, without its language
    values: Values
    cards: dict[str, dict[str, Any]]  # finding fact id → {tag, title, line, chart, href}
    refs: dict[str, str]  # fact id → where it lives
    kpis: list[dict[str, Any]]  # the headline numbers, computed (code, never the model)
    stakes: list[dict[str, Any]]  # the money at stake, computed: {fact, value, unit, href, why}
    forecasts: list[dict[str, Any]]  # What's ahead's cards, computed


def _split_money(money: str) -> tuple[str, str]:
    """ "₹4.59 Cr at risk" → ("₹4.59 Cr", "at risk"); "₹5.8 L / month" → ("₹5.8 L", "/ month")."""
    m = NUM.search(money)
    if m is None:
        return money, ""
    return m.group(0), money[m.end() :].strip()


def facts(
    briefs: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    names: dict[str, str] | None = None,
) -> Facts:
    """The run's computed text as facts, its figures noted for the check; the tiles, the stakes
    and the forecast cards computed by code.

    briefs: [{name, href, body}] (each brief's latest issue body); findings: Sweep's open
    findings [{fp, title, line, money, state}] in rank order; metrics: [{key, name,
    description}]; names: metric key → its name (a finding card's tag)."""
    names = names or {}
    s = Values()
    out_briefs: list[dict[str, Any]] = []
    cards: dict[str, dict[str, Any]] = {}
    fids: dict[str, str] = {}  # the fact an action points at
    kpis: list[dict[str, Any]] = []
    stakes: list[dict[str, Any]] = []
    forecasts: list[dict[str, Any]] = []
    for bi, b in enumerate(briefs):
        body = b["body"]
        href = b.get("href", "")
        items = []
        for ii, it in enumerate(body.get("items") or []):
            fid = f"b{bi + 1}i{ii + 1}"
            ihref = f"/sweep/finding/{it['fingerprint']}" if it.get("fingerprint") else href
            fids[fid] = ihref
            cons = (it.get("consequence") or {}).get("text") or ""
            items.append(
                _fact(
                    s,
                    fid,
                    "finding",
                    it.get("title", ""),
                    ihref,
                    line=it.get("line") or "",
                    consequence=cons,
                    money=it.get("money") or "",
                )
            )
            cards[fid] = {
                "tag": names.get(it.get("metric", ""), it.get("label", "")),
                "title": it.get("title", ""),
                "line": it.get("line") or "",
                "chart": it.get("chart"),
                "href": ihref,
            }
            if it.get("money"):
                value, unit = _split_money(it["money"])
                stakes.append(
                    {"fact": fid, "value": value, "unit": unit, "href": ihref, "why": cons}
                )
        tiles = []
        for ti, t in enumerate(body.get("tiles") or []):
            note = (t.get("comparison") or {}).get("text") or ""
            tiles.append(
                _fact(
                    s,
                    f"b{bi + 1}t{ti + 1}",
                    "number",
                    f"{t.get('label', '')}: {t.get('value', '')}",
                    href,
                    comparison=note,
                )
            )
            if bi == 0 and len(kpis) < KPIS:
                kpis.append(
                    {
                        "label": t.get("label", ""),
                        "value": t.get("value", ""),
                        "tone": t.get("role", "neutral"),
                        "note": note,
                    }
                )
        ahead = []
        for ai, r in enumerate(body.get("ahead") or []):
            fid = f"b{bi + 1}a{ai + 1}"
            ahref = f"{href}#ahead-{r.get('metric', ai + 1)}"  # its own row in What's ahead
            fids[fid] = ahref
            says = f"{r['name']}: {r.get('says') or r.get('why') or ''}"
            ahead.append(_fact(s, fid, "forecast", says, ahref))
            if r.get("card"):
                forecasts.append({**r["card"], "href": ahref})
        out_briefs.append(
            {
                "name": b["name"],
                "title": s.note(body.get("title", ""), href),
                "line": s.note(body.get("line", ""), href),
                # 0.28.4: the brief's opening names each item by its title; Big Picture still
                # reads every item's detail line, so it keeps all the night's figures
                "situation": s.note(
                    " ".join(
                        x
                        for x in [
                            body.get("situation", ""),
                            *[str(i.get("line") or "") for i in body.get("items") or []],
                        ]
                        if x
                    ),
                    href,
                ),
                "numbers": tiles,
                "findings": items,
                "goals": [s.note(g.get("text", ""), href) for g in body.get("goals") or []],
                "ahead": ahead,
                "ahead_note": s.note(body.get("ahead_note") or "", href),
            }
        )
    sweep = []
    for fi, f in enumerate(findings):
        fid = f"f{fi + 1}"
        href = f"/sweep/finding/{f['fp']}"
        fids[fid] = href
        sweep.append(
            _fact(
                s,
                fid,
                "finding",
                f.get("title", ""),
                href,
                line=f.get("line") or "",
                money=f.get("money") or "",
                state=f.get("state") or "",
            )
        )
    words = [
        {"name": m["name"], "means": s.note(m.get("description") or "")}
        for m in metrics
        if m.get("name")
    ]
    inputs = {
        "briefs": out_briefs,
        "sweep": sweep,
        "numbers_mean": words,
        "stakes": [
            {"fact": x["fact"], "money": x["value"] + (" " + x["unit"] if x["unit"] else "")}
            for x in stakes[:STAKES]
        ],
    }
    # goal forecasts first, then the rest, at most two
    forecasts.sort(key=lambda c: c.get("target") is None)
    return Facts(inputs, s, cards, fids, kpis, stakes[:STAKES], forecasts[:FORECASTS])


def model_input(inputs: dict[str, Any], language: str) -> dict[str, Any]:
    return {**inputs, "language": LANGUAGES.get(language, "English")}


# --- the model's output → the report ------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    body: dict[str, Any] | None  # None: too little survived the check
    guard: str  # passed · dropped:N · too-little


def fill(output: dict[str, Any] | None, f: Facts, language: str) -> Report:
    """The model's words, checked, around the computed parts. A finding card's or a stake's words
    that the check drops fall back to the computed text (its own title and line)."""
    vals = f.values
    if not isinstance(output, dict):
        return Report(None, "failed:not-json")
    dropped = 0

    def one(x: Any) -> list[dict[str, str]] | None:
        nonlocal dropped
        if x is None or (isinstance(x, str) and not x.strip()):
            return None
        seg = _sentence(x, vals)
        if seg is None:
            dropped += 1
        return seg

    def computed(text: str) -> list[dict[str, str]]:
        return _sentence(text, vals) or [{"t": text}]

    lead = one(output.get("one_line"))
    body: dict[str, Any] = {"language": language, "one_line": lead}
    body["summary"] = [x for x in (one(s) for s in _strs(output.get("summary"))) if x][:3]
    body["kpis"] = f.kpis
    # What's happening: one card per finding, the model's order first, then by rank
    said = {
        str(x.get("fact")): x for x in _dicts(output.get("findings")) if x.get("fact") in f.cards
    }
    order = [k for k in said] + [k for k in f.cards if k not in said and f.cards[k]["chart"]]
    cards = []
    for k in order[:CHARTS]:
        c, w = f.cards[k], said.get(k, {})
        cards.append(
            {
                "fact": k,
                "tag": c["tag"],
                "title": one(w.get("title")) or computed(c["title"]),
                "sentence": one(w.get("sentence")) or computed(c["line"] or c["title"]),
                "chart": c["chart"],
                "href": c["href"],
            }
        )
    body["findings"] = cards
    # Why it matters: the money, computed; the model's words for what each is
    desc = {str(x.get("fact")): x.get("desc") for x in _dicts(output.get("stakes"))}
    body["stakes_note"] = one(output.get("stakes_note"))
    body["stakes"] = [
        {
            "value": x["value"],
            "unit": x["unit"],
            "href": x["href"],
            "desc": one(desc.get(x["fact"])) or computed(x["why"] or f.cards[x["fact"]]["title"]),
        }
        for x in f.stakes
    ]
    look = []
    for a in _dicts(output.get("look_first"))[:3]:
        seg = one(a.get("text"))
        if seg is None:
            continue
        fact = str(a.get("fact") or "")
        look.append(
            {
                "text": seg,
                "href": f.refs.get(fact, ""),
                "tag": (f.cards.get(fact) or {}).get("tag", ""),
            }
        )
    body["look_first"] = look
    body["forecasts"] = f.forecasts
    words = []
    for w in _dicts(output.get("words"))[:5]:
        term, means = one(w.get("term")), one(w.get("meaning"))
        if term is None or means is None:
            continue
        words.append({"term": term, "meaning": means})
    body["words"] = words
    if lead is None or not (body["summary"] or cards):
        return Report(None, "too-little")
    return Report(body, f"dropped:{dropped}" if dropped else "passed")


def _dicts(xs: Any) -> list[dict[str, Any]]:
    return [x for x in xs if isinstance(x, dict)] if isinstance(xs, list) else []


def _strs(xs: Any) -> list[Any]:
    return list(xs) if isinstance(xs, list) else []


def plain(segments: list[dict[str, str]] | None) -> str:
    """A sentence's text, for the email's text part and the tests."""
    return "".join(s["t"] for s in segments or [])
