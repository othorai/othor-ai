# SPDX-License-Identifier: Apache-2.0
"""Sweep's words for a finding (M6b, docs/04 §7 and §9): title, line, consequence, why you
haven't seen this, the question to ask, possible reasons with what would settle each, the rows
it shows. Rule-based templates filled from the finding's computed slots only: no model, and no
number that isn't in the finding (non-negotiable 1; `tests/unit/engine/test_sweep_text.py`).

Pure: the same finding and spec give the same text. `TEXT_VERSION` is stored with it.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from othor.core import fmt

TEXT_VERSION = "1"

LABELS = {
    "net_hiding_gross": "Net hiding gross",
    "changepoint": "Changepoint",
    "rising_variance": "Rising variance",
    "broken_relationship": "Broken relationship",
    "lead_lag": "Lead/lag",
    "concentration": "Concentration",
    "missing_records": "Missing records",
    "process_artefact": "Process artefact",
    "cross_source": "Cross-source contradiction",
    "bimodal_average": "Bimodal average",
    "forecast_breach": "Forecast breach",
}


# The owner's short names, under each bar of Sweep's detector strip (M15.7b, 13 Nov 2026).
SHORT = {
    "net_hiding_gross": "Net/gross",
    "changepoint": "Shift",
    "concentration": "Skew",
    "forecast_breach": "Breach",
    "rising_variance": "Volatile",
    "broken_relationship": "Decoupled",
    "cross_source": "Conflict",
    "missing_records": "Gaps",
    "lead_lag": "Lag",
    "bimodal_average": "Bimodal",
    "process_artefact": "Artefact",
}

# What each detector finds, in plain words (docs/04 §3), keyed by the registry: the strip's cards
# and the website's list (site_content.CATCHES_C) read the same words.
CATCHES = {
    "net_hiding_gross": "A flat total hiding moves that cancel out underneath",
    "changepoint": "The week a baseline moved, and stayed moved",
    "forecast_breach": "An actual outside the band stated before it arrived, or a goal slipping",
    "rising_variance": "A spread widening before the average moves",
    "broken_relationship": "Two numbers that moved together, until they didn’t",
    "lead_lag": "One number that moves weeks before another",
    "concentration": "A few names carrying most of the value",
    "missing_records": "Customers still marked active who went silent",
    "process_artefact": "Values piling up just under a limit or a round number",
    "cross_source": "Two systems giving the same number different values",
    "bimodal_average": "An average sitting between two very different groups",
}


def money(v: Any) -> str:
    """The one money rule (fmt.money, 21 Nov 2026): '₹3.6 Cr', '₹45.3 L', '$1.25M', '$58K'."""
    return fmt.money(Decimal(str(v)))


def _tidy(s: str) -> str:
    """'$45.0K' -> '$45K', '$1.20M' -> '$1.2M' (USD keeps no trailing zeros)."""
    import re

    return re.sub(r"(\d)\.?0+([KMB])$", lambda m: m[1] + m[2], s) if "." in s else s


def money_col(values: list[Any]) -> list[str]:
    """A table column of money in one unit (5c, M12b): crore when any value reaches ₹10 L, else
    lakh, so "₹0.11 Cr" never sits above "₹8.0 L"; millions or thousands in a USD company."""
    ds = [Decimal(str(v)) for v in values]
    if any(abs(d) >= Decimal("1000000") for d in ds):
        return [money(d) if abs(d) >= Decimal("1000000") else _cr(d) for d in ds]
    return [money(d) for d in ds]


def _cr(d: Decimal) -> str:
    """In the big unit: crore (₹0.11 Cr), or millions in a USD company ($0.11M)."""
    if fmt.is_usd():
        return _tidy(fmt.inr(d, unit="m", decimals=2))
    s = fmt.inr(d, unit="cr", decimals=2)
    head, _, tail = s.partition(" ")
    if "." in head:
        head = head.rstrip("0").rstrip(".")
    return f"{head} {tail}"


def money_tag(f: dict[str, Any]) -> str:
    """The card's money: '₹3.6 Cr', '₹0.8 Cr at risk', '₹0.21 Cr / month'."""
    slots = f.get("slots", {})
    if f["detector"] == "broken_relationship" and slots.get("money_per_month"):
        return f"{money(slots['money_per_month'])} / month"
    if f.get("money") is None:
        return ""
    if f["detector"] == "rising_variance" or " at risk " in f" {f.get('money_basis') or ''} ":
        return f"{money(f['money'])} at risk"
    return money(f["money"])


def _d(iso: str | None) -> str:
    return fmt.day_month(dt.date.fromisoformat(str(iso)[:10])) if iso else ""


def _month(iso: str) -> str:
    return dt.date.fromisoformat(str(iso)[:10]).strftime("%B")


def _name(spec: dict[str, Any], metric: str) -> str:
    return str(spec.get("metrics", {}).get(metric, {}).get("name") or metric.replace("_", " "))


def _lower(s: str) -> str:
    return fmt.lower_first(s)


def _unit(f: dict[str, Any], v: Any) -> str:
    u = f.get("slots", {}).get("unit")
    if u == "inr":
        return money(v)
    if u == "days":
        return f"{fmt.num(v, decimals=1)} days"
    if u in ("percent", "ratio"):
        return fmt.pct(v, decimals=1)
    return fmt.num(v, decimals=1)


def _source(spec: dict[str, Any], f: dict[str, Any]) -> str:
    fam = f.get("family", "")
    if fam.startswith("entity:"):
        e = spec.get("probes", {}).get("entities", {}).get(fam.split(":", 1)[1], {})
        if e.get("source"):
            return str(e["source"])
    label = spec.get("metrics", {}).get(f["metric"], {}).get("source_label") or ""
    return str(label).split(" · ")[-1] if " · " in str(label) else str(label) or "your sources"


def owner(spec: dict[str, Any], f: dict[str, Any]) -> dict[str, str] | None:
    """The Ontology owner of the finding's area (name and role), the first by name."""
    from othor.engine.asks import owners_for

    names = owners_for(spec, f)
    for o in sorted(spec.get("owners", []), key=lambda o: o["name"]):
        if o["name"] in names:
            return {"name": o["name"], "role": o.get("role", "")}
    return None


def owners(spec: dict[str, Any], f: dict[str, Any]) -> list[dict[str, str]]:
    from othor.engine.asks import owners_for

    names = owners_for(spec, f)
    return [
        {"name": o["name"], "role": o.get("role", "")}
        for o in sorted(spec.get("owners", []), key=lambda o: o["name"])
        if o["name"] in names
    ]


def words(f: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Every text of one finding, as plain data."""
    fn = _BY.get(f["detector"], _generic)
    out = {
        "version": TEXT_VERSION,
        "label": LABELS.get(f["detector"], f["detector"]),
        "money": money_tag(f),
        "reasons": [],
        "measured": [],
        "rows": None,
        "settle": [],
        **fn(f, spec),
    }
    who = owners(spec, f)
    out["owners"] = who
    if out.get("question") and who:
        out["question"] = {"text": out["question"], "to": who}
    elif out.get("question"):
        out["question"] = {"text": out["question"], "to": []}
    return out


# --- per detector ------------------------------------------------------------------------------


def _entity_words(spec: dict[str, Any], f: dict[str, Any]) -> dict[str, Any]:
    """What an entity is called and what it does, from the Business Model (an insurer's partner
    banks issue policies; 24 Nov 2026). Without words: customers that invoice, as before."""
    fam = str(f.get("family", ""))
    e = (
        spec.get("probes", {}).get("entities", {}).get(fam.split(":", 1)[1], {})
        if fam.startswith("entity:")
        else {}
    )
    w = e.get("words") or {}
    return {
        "noun": w.get("noun") or "customer",
        "plural": w.get("plural") or "customers",
        "did": w.get("did") or "invoiced",
        "doing": w.get("doing") or "invoicing",
        "event": w.get("event") or "invoice",
        "own": bool(w),
    }


def _missing(f: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    sl = f["slots"]
    src = _source(spec, f)
    w = _entity_words(spec, f)
    if f["state"] == "measured":  # the group of active customers gone silent
        weeks = round(float(sl.get("gap_lengthened_weeks") or 0))
        gaps = (
            f" Their reorder gaps lengthened for about {fmt.num(weeks)} weeks"
            " before they went quiet."
            if weeks >= 2
            else ""
        )
        rows = [
            [
                e["name"],
                _d(e.get("last")),
                f"{fmt.num(e['silent_days'])} d",
                f"{fmt.num(e['gap_before']['from'])} → {fmt.num(e['gap_before']['to'])} d"
                if e.get("gap_before")
                else "",
                m12,
            ]
            for e, m12 in zip(
                f.get("evidence", []),
                money_col([e["last_12m"] for e in f.get("evidence", [])]),
                strict=True,
            )
        ]
        return {
            "title": (
                f"{fmt.num(sl['count'])} "
                + (
                    f"{w['noun']} still marked active hasn’t"
                    if sl["count"] == 1
                    else f"{w['plural']} still marked active haven’t"
                )
                + f" {w['did']} in {fmt.num(sl['quiet_days'])}+ days"
            ),
            "line": f"Together {money(sl['total_12m'])} over the last 12 months.{gaps}",
            "consequence": f"{money(sl['total_12m'])} a year outside view",
            "why_unseen": (
                f"{src} has no status for a {w['noun']} that goes quiet, so no row shows it."
                if w["own"]
                else f"{src} has no churn status. Customers don’t cancel; they stop ordering, so "
                "no row is created."
            ),
            "question": (
                f"Who looks after a {w['noun']} that’s still active but has gone quiet?"
                if w["own"]
                else "Who owns an account that hasn’t cancelled but has stopped buying?"
            ),
            "rows": {
                "headers": [
                    w["noun"].capitalize() if w["own"] else "Account",
                    f"Last {w['event']}",
                    "Silent",
                    "Reorder gap",
                    "Last 12 m",
                ],
                "rows": rows,
                "noun": w["plural"] if w["own"] else "accounts",
                "total": int(sl["count"]),
                "note": "No chart by design: these accounts produce no rows.",
            },
            "chart": False,
        }
    # one regular customer gone quiet: Not determinable
    name = sl.get("name") or (f.get("entities") or [""])[0]
    settle_by = {
        "competitor": "Needs deal activity or a call",
        "on_hold": "Needs an answer from the account’s owner",
        "billing_moved": "Needs a customer list with tax ids",
    }
    reasons = []
    for r in f.get("reasons", []):
        chk = r.get("check") or {}
        done = (
            "No new customer with the same GSTIN"
            if chk.get("kind") == "same_identity" and r.get("ruled_out")
            else (
                f"A newer customer shares the GSTIN: {', '.join(chk.get('found') or [])}"
                if chk.get("kind") == "same_identity" and chk.get("found")
                else settle_by.get(r["key"], "")
            )
        )
        reasons.append(
            {
                "text": r["text"][:1].upper() + r["text"][1:],
                "settle": done,
                "ruled_out": bool(r.get("ruled_out")),
            }
        )
    lo, hi = sl.get("min_days"), sl.get("max_days")
    rhythm = f"Every {fmt.num(sl.get('median_days', 0))} days"
    if lo is not None and hi is not None:
        rhythm += f" ({fmt.num(lo)}–{fmt.num(hi)})"
    if sl.get("years"):
        rhythm += f" over {fmt.num(sl['years'], decimals=1)} years"
    first = owner(spec, f)
    settle = []
    if first:
        settle.append(f"Ask {first['name']} (the question below)")
    settle.append("Or connect a deal list with activity dates; this finding re-runs tonight")
    settle.append(f"If it is a known pause, add it; Sweep stops flagging {name} until the end date")
    return {
        "title": f"{name} stopped {w['doing']} on {_d(sl.get('last'))}",
        "line": "The gap is measured. Why it happened can’t be told from the data Othor AI reads.",
        "consequence": "needs deal activity or a call",
        "why_unseen": (
            f"{src} has no status for a {w['noun']} that stops {w['doing']}, so no row shows it."
            if w["own"]
            else f"{src} has no status for a customer who stops ordering, so no row shows it."
        ),
        "question": f"Have {name}’s orders moved elsewhere, or are they on hold?",
        "measured": [
            [f"Last {w['event']}", _d(sl.get("last"))],
            ["Usual rhythm", rhythm],
            ["Gap now", f"{fmt.num(sl.get('gap_days', 0))} days"],
            ["Value at risk", money(f["money"]) if f.get("money") else ""],
        ],
        "reasons": reasons,
        "settle": settle,
        "chart": False,
    }


def _slowest(f: dict[str, Any], sl: dict[str, Any]) -> str:
    """0.28.4 (100-night test): "now take 4.5 days, up from 4.6 days". The word follows the
    figures as they're written; the same figure says the times are scattered, not slower."""
    now, before = _unit(f, sl["p90_recent"]), _unit(f, sl["p90_prior"])
    if now == before:
        return f"The slowest tenth still take about {now}; the times in between scatter more."
    up = Decimal(str(sl["p90_recent"])) > Decimal(str(sl["p90_prior"]))
    return f"The slowest tenth now take {now}, {'up' if up else 'down'} from {before}."


def _variance(f: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    sl = f["slots"]
    sd0, sd1 = Decimal(sl["sd_prior"]), Decimal(sl["sd_recent"])
    ratio = sd1 / sd0 if sd0 else Decimal(0)
    verb = "tripled" if ratio >= 3 else "doubled" if ratio >= 2 else "widened"
    metric = _name(spec, f["metric"])
    where = f" at {f['breakdown_value']}" if f.get("breakdown_value") else ""
    began = sl.get("since") or sl["started"]  # the first start reported, while it stays open
    return {
        "title": (
            f"{metric} spread{where} {verb} from {_d(began)} while the average held at "
            f"{_unit(f, sl['mean_recent'])}"
        ),
        "line": _slowest(f, sl),
        "consequence": "spread usually comes before complaints",
        "why_unseen": "Reports show the average, and the average has not moved.",
        "question": f"What changed in {_lower(metric)}{where} in the week of {_d(began)}?",
    }


def _pair(f: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    sl = f["slots"]
    a, b = (f.get("entities") or [f["metric"], ""])[:2]
    na, nb = _name(spec, a), _name(spec, b)
    return {
        "title": (
            f"{na} tracked {_lower(nb)} for {fmt.num(sl['weeks_before'])} weeks, then decoupled "
            f"on {_d(sl['break'])}"
        ),
        "line": (
            f"Correlation fell from {fmt.num(sl['r_before'], decimals=2)} to "
            f"{fmt.num(sl['r_after'], decimals=2)}. {na} {_per(spec, b, nb)} is up "
            f"{fmt.pct(sl['ratio_change'])}."
        ),
        "consequence": f"{money(sl['money_per_month'])} a month at the new ratio",
        "why_unseen": (
            f"{na} and {_lower(nb)} sit in different places; no report puts them side by side."
        ),
        "question": f"What changed in {_lower(na)} in the week of {_d(sl['break'])}?",
    }


PER = {"metres": "per metre", "count": "per unit", "days": "per day"}


def _per(spec: dict[str, Any], metric: str, name: str) -> str:
    unit = spec.get("metrics", {}).get(metric, {}).get("unit")
    return PER.get(str(unit), f"per unit of {_lower(name)}")


def _singular(noun: str) -> str:
    return noun[:-1] if noun.endswith("s") else noun


def _cross(f: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    sl = f["slots"]
    if "count" in sl:
        noun, n = sl.get("noun") or "records", int(sl["count"])
        lt, _, ls = str(sl.get("left", "")).partition(" in ")
        rtab, _, rs = str(sl.get("right", "")).partition(" in ")
        lsrc = sl.get("left_source") or ls or lt
        rsrc = sl.get("right_source") or rs or rtab
        rt = _singular(sl.get("right_table") or rtab or "match")
        reasons = [
            f"The {rt} is late. Check the {fmt.num(n)} {noun} against open drafts in {rsrc}.",
            f"The {noun} were cancelled after the event, and {lsrc} was not updated.",
            f"The two systems mean different things by “{_singular(noun)}”.",
        ]
        ev = f.get("evidence", [])
        rows = [
            [e["key"], e.get("entity", ""), f"{fmt.num(e['days'])} d", v]
            for e, v in zip(ev, money_col([e["value"] for e in ev]), strict=True)
        ]
        return {
            "title": (
                f"{fmt.num(n)} {noun} in {lsrc} have no matching {rt} in {rsrc} after "
                f"{fmt.num(sl['lag_days'])} days"
            ),
            "line": (
                f"{money(f['money'])} in {lsrc} with no matching {rt} in {rsrc} after "
                f"{fmt.num(sl['lag_days'])} days. This is a mismatch between two systems, not a "
                "revenue figure."
            ),
            "consequence": "Check in the source system before acting",
            "why_unseen": "Each system is consistent on its own and owned by a different team.",
            "question": f"Which system is right about these {fmt.num(n)} {noun}?",
            "reasons": [{"text": r, "settle": "", "ruled_out": False} for r in reasons],
            "rows": {
                "headers": [_singular(noun).capitalize(), "Customer", "Since", "Value"],
                "rows": rows,
                "noun": noun,
                "total": n,
                "note": None,
            },
            "gap": {"count": n, "value": money(f["money"]), "noun": noun, "rsrc": rsrc, "rt": rt},
        }
    period = sl.get("period", "")
    grain = sl.get("grain") or "month"
    when = (
        f"the last complete week ({_d(period)})"
        if grain == "week"
        else f"the last complete month ({_month(period)})"
    )
    return {
        "title": (
            f"{sl.get('a', '')} and {sl.get('b', '')} differ by {money(f['money'])} for {when}"
        ),
        "line": (
            f"{money(f['money'])} between the two for {when}. This is a mismatch between two "
            "systems, not a revenue figure."
        ),
        "consequence": "Check in the source system before acting",
        "why_unseen": "Each system is consistent on its own and owned by a different team.",
        "question": f"Which figure is right for {when}?",
        "reasons": [
            {"text": "One system books later than the other.", "settle": "", "ruled_out": False},
            {"text": "A correction was made in one system only.", "settle": "", "ruled_out": False},
            {"text": "The two define the measure differently.", "settle": "", "ruled_out": False},
        ],
    }


def _nhg(f: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    sl = f["slots"]
    fall, rise = sl["fall"], sl["rise"]
    metric = _name(spec, f["metric"])
    month = _month(sl["period"])
    nxt = (dt.date.fromisoformat(sl["period"]).replace(day=28) + dt.timedelta(days=4)).replace(
        day=1
    )
    out: dict[str, Any] = {
        "title": (
            f"{fall['name']} fell {money(abs(Decimal(fall['change'])))}; "
            f"{rise['name']}’s rise hid it"
        ),
        "line": (
            f"{metric} moved {money(sl['net'])} from {_month(sl['previous'])} to {month}: "
            f"{fall['name']} {money(fall['change'])}, {rise['name']} +{money(rise['change'])}."
        ),
        # the close it endangers: the month now running (the brief counts the days to it)
        "consequence": f"{nxt:%B} close",
        "why_unseen": f"The headline hides it: the total moved only {money(sl['net'])}.",
        "question": f"What changed for {fall['name']} customers in {month}?",
    }
    d = sl.get("drill") or {}
    if d.get("accounts"):
        from othor.briefs.title import drill_sentence

        said = drill_sentence(d)
        if said:
            out["line"] = f"{out['line']} {said}"
        out["rows"] = {
            "headers": ["Account", "Change", "Last invoice", "Status"],
            "rows": [
                [
                    a["name"],
                    ch,
                    _d(a.get("last")) if a.get("last") else "—",
                    _paused(a.get("paused")),
                ]
                for a, ch in zip(
                    d["accounts"], money_col([a["change"] for a in d["accounts"]]), strict=True
                )
            ],
            "noun": "accounts",
            "total": len(d.get("rows") or d["accounts"]),
            "note": None,
        }
    return out


def _paused(p: dict[str, Any] | None) -> str:
    """An account under a known pause (decided 6 Nov 2026): 'Paused · your answer, 30 Oct'."""
    if not p:
        return ""
    return "Paused" + (f" · your answer, {_d(p['answered_on'])}" if p.get("answered_on") else "")


def _concentration(f: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    sl = f["slots"]
    metric = _name(spec, f["metric"])
    names = sl.get("names") or []
    n = int(sl.get("top_n") or len(names))
    words_n = {2: "Two", 3: "Three", 4: "Four", 5: "Five"}.get(n, fmt.num(n))
    return {
        "title": f"{words_n} customers now hold {fmt.pct(sl['share_now'])} of the {_lower(metric)}",
        "line": (
            f"Up from {fmt.pct(sl['share_start'])} in {_month(sl['start'])}: {', '.join(names)}."
        ),
        "consequence": "the order book's next invoices",
        "why_unseen": f"No report ranks customers by their share of the {_lower(metric)}.",
        "question": (
            f"Is it a plan that {words_n.lower()} customers hold {fmt.pct(sl['share_now'])} "
            f"of the {_lower(metric)}?"
        ),
    }


def _step(f: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    sl = f["slots"]
    metric = _name(spec, f["metric"])
    up = Decimal(sl["after"]) > Decimal(sl["before"])
    return {
        "title": (
            f"{metric} stepped {'up' if up else 'down'} in the week of {_d(sl['change_week'])}"
        ),
        "line": (
            f"From {_unit(f, sl['before'])} to {_unit(f, sl['after'])}, held for "
            f"{fmt.num(sl['weeks_held'])} weeks."
            # #138 (owner, 25 Nov 2026): a step from 0 has no share of its linked ₹ to read
            + (
                " The money at risk can't be sized: it started from 0."
                if sl.get("money_unsized")
                else ""
            )
        ),
        # 0.33.1: the line named no date ("It has held since"); it says the week the step began
        "consequence": f"it has held since the week of {_d(sl['change_week'])}",
        "why_unseen": (
            "Each week looks normal on its own; the step shows only against the weeks before."
        ),
        "question": f"What changed in {_lower(metric)} in the week of {_d(sl['change_week'])}?",
    }


def _breach(f: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    sl = f["slots"]
    metric = _name(spec, f["metric"])
    if sl.get("kind") == "goal":
        return {
            "title": (
                f"{metric} is tracking {money(sl['projection'])} against the "
                f"{money(sl['target'])} goal"
            ),
            "line": (
                f"{money(sl['to_date'])} to date plus the stored forecast for the rest of the "
                f"year: {money(sl['short_by'])} short by {_d(sl['by'])}."
                if sl.get("to_date")
                else f"The forecast for {_month(sl['by'])} is {money(sl['projection'])}."
            ),
            "consequence": f"the goal date, {_d(sl['by'])}",
            "why_unseen": "Each month looks close to plan; the shortfall shows only over the year.",
            "question": f"What would close the {money(sl['short_by'])} gap by {_d(sl['by'])}?",
        }
    when = f"the week of {_d(sl['period'])}" if sl.get("grain") == "week" else _month(sl["period"])
    side = "above" if f["direction"] == "up" else "under"
    from othor.briefs import values as vals

    unit = sl.get("unit")

    def value(v: Any) -> str:  # in the number's own unit, as every page writes it (0.28.4)
        return vals.text(unit, v)

    def spread(lo: Any, hi: Any) -> str:
        return vals.text_band(unit, lo, hi)

    return {
        "title": (
            f"{metric} came in {fmt.pct(abs(Decimal(sl['share'])))} {side} its forecast for {when}"
        ),
        "line": (
            f"{value(sl['actual'])} against the {value(sl['point'])} expected on "
            f"{_d(sl['forecast_as_of'])} (likely range {spread(sl['lower'], sl['upper'])})."
        ),
        "consequence": f"{when} closed outside its expected range",
        "why_unseen": "The forecast was stored before the period; reports show the actual alone.",
        "question": f"What made {_lower(metric)} {side} its range in {when}?",
    }


def _generic(f: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    metric = _name(spec, f["metric"])
    return {
        "title": f"{LABELS.get(f['detector'], f['detector'])} on {_lower(metric)}",
        "line": f"Money at stake: {money(f['money'])}." if f.get("money") else "",
        "consequence": "",
        "why_unseen": f"{metric} isn’t shown this way in any of your reports.",
        "question": None,
    }


_BY = {
    "missing_records": _missing,
    "rising_variance": _variance,
    "broken_relationship": _pair,
    "cross_source": _cross,
    "net_hiding_gross": _nhg,
    "concentration": _concentration,
    "changepoint": _step,
    "forecast_breach": _breach,
}
_CORE = frozenset(_BY) | frozenset(LABELS)

# Sweep always shows 12 checks (owner, 21 Nov 2026: "keep it always out of 12… if not activated
# show it as grey"): External Signals' check has its place even where it isn't on. Its words live
# here, apart from the core's LABELS, SHORT and CATCHES, which the website lists as the core checks.
OUTSIDE = "outside_moved"
OUTSIDE_WORDS = {
    "label": "Outside signal moved",
    "short": "External",  # "Ext" on its square (owner, 21 Nov 2026)
    "catches": "An outside number you linked moving beyond its usual range, and what it meant "
    "for yours",
}
PLUGIN_SHORT: dict[str, str] = {}  # a plug-in check's name on its square (register_words)


def short_of(detector: str) -> str:
    """A check's name on Sweep's squares: the owner's short name, a plug-in's own, or its label."""
    return SHORT.get(detector) or PLUGIN_SHORT.get(detector) or LABELS.get(detector, detector)


def has_words(detector: str) -> bool:
    """Whether a plug-in registered this detector's words (its feature is installed)."""
    return detector in _BY


def register_words(detector: str, label: str, fn: Any, short: str | None = None) -> None:
    """A plug-in detector's words (Advanced Intelligence, External Signals): its label in Sweep
    and a function (finding, spec) -> {title, line, consequence, why_unseen, question}, filled
    from the finding's computed slots only (non-negotiable 1). A core detector can't be taken."""
    if detector in _CORE:
        raise ValueError(f"detector {detector!r} is the core's")
    LABELS[detector] = label
    if short:  # its name on Sweep's check squares (owner, 21 Nov 2026)
        PLUGIN_SHORT[detector] = short
    _BY[detector] = fn


# --- Coverage and quiet night (5a, 5f, 10i) ----------------------------------------------------

RESULT_WORD = {
    "net_hiding_gross": "clean",
    "changepoint": "clean",
    "concentration": "steady",
    "rising_variance": "stable",
    "broken_relationship": "holding",
    "cross_source": "matched",
    "missing_records": "none new",
    "lead_lag": "none new",
    "bimodal_average": "single group",
    "process_artefact": "no pile-up",
    "forecast_breach": "in band",
}

NEEDS = {
    "entity": "Needs a customer list with a status (a breakdown entity in your Business Model)",
    "reconcile": "Needs a rule saying which records should match across sources",
    "pair": "Needs two metrics that should move together",
    "threshold": "Needs an approval limit or another threshold",
    "distribution": "Needs a metric's per-order values",
    "forecast": "Starts as periods complete: each forecast is stored before its period",
}


def count(n: Any, unit: str) -> str:
    """'25 metrics', '1 pair', '139 customers'."""
    word = {"entities": "customers", "breakdowns": "breakdowns"}.get(unit, unit)
    if int(n) == 1:
        word = {"series": "series", "entities": "customer", "customers": "customer"}.get(
            unit, word[:-1] if word.endswith("s") else word
        )
    return f"{fmt.num(n)} {word}"


def checked(c: dict[str, Any], found: int) -> str:
    """What one detector checked tonight: '25 metrics · clean', '37 checks · 1 new'."""
    n = count(c["checked"], c["unit"])
    if c["status"] == "could_not_run":
        return NEEDS.get(c.get("needs") or "", "Could not run")
    if found:
        return f"{n} · {fmt.num(found)} new"
    return f"{n} · {RESULT_WORD.get(c['detector'], 'clean')}"


# What each detector tested, as one question (M15.3, the finding panel's Test tab): {metric} is
# the number's name, {breakdown} its breakdown, {period} the month tested. Code-written; no model.
TESTED = {
    "net_hiding_gross": "Is {period}’s flat total hiding {breakdown}s that moved in opposite "
    "directions?",
    "changepoint": "Did {metric} settle at a new level, and is the shift real?",
    "rising_variance": "Is the spread of {metric} widening while its average holds?",
    "broken_relationship": "Does {metric} still move with the number it used to follow?",
    "lead_lag": "Does {metric} lead another number, and has that lead changed?",
    "concentration": "Does {metric} now rest on fewer customers than before?",
    "missing_records": "Has someone who used to appear regularly in {metric} gone quiet?",
    "process_artefact": "Are values of {metric} bunching just under a limit?",
    "cross_source": "Do the same records still match across your two sources?",
    "bimodal_average": "Is the average of {metric} hiding two different groups?",
    "forecast_breach": "Did {metric} land outside the band forecast before the period began?",
}
