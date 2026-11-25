# SPDX-License-Identifier: Apache-2.0
"""Guards on every model call (docs/06 "Guards"), enforced in code.

1. Inputs never carry personal columns, raw rows, credentials or other companies' data.
2. No number from model output is used unless it appears in the input JSON.
3. A model sentence whose direction or count contradicts the computed result is dropped (#120).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# the text's numbers, shared with the Ontology's catalogue check (which may not import othor.ai)
from othor.core.numerals import _NUM as _NUM
from othor.core.numerals import _WORD as _WORD
from othor.core.numerals import _word_numbers as _word_numbers
from othor.core.numerals import numerals as numerals

_FORBIDDEN_KEYS = {"password", "secret", "token", "access_token", "refresh_token", "rows", "values"}


class InputRefused(ValueError):
    """The prompt input broke a guard; nothing was sent."""


_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def _date_numerals(text: str) -> set[str]:
    """The numerals of every ISO date in the text as the app displays it (#123): 2026-11-01
    is also written "1 Nov 2026", "Nov 1" or "1 November 2026", so 1, 11 and 2026 count."""
    out: set[str] = set()
    for m in _ISO_DATE.finditer(text):
        year, month, day = (int(g) for g in m.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            out |= {str(year), str(month), str(day)}
    return out


def allowed_numerals(inputs: Any) -> set[str]:
    """The numerals a model may write: those in the input JSON, plus the day, month and year
    of every ISO date in it."""
    text = json.dumps(inputs, ensure_ascii=False, default=str)
    return numerals(text) | _date_numerals(text)


def numbers_grounded(output: str, inputs: Any) -> bool:
    """Every numeral in the output appears in the input JSON (an input date's day, month and
    year count in any display form)."""
    return numerals(output) <= allowed_numerals(inputs)


def check_input(inputs: Any, personal_names: Iterable[str] = ()) -> None:
    """Raise if the input holds forbidden keys or names a personal column."""
    text = json.dumps(inputs, ensure_ascii=False, default=str).lower()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).lower() in _FORBIDDEN_KEYS:
                    raise InputRefused(f"forbidden key in model input: {k}")
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(inputs)
    for p in personal_names:
        if re.search(r'"' + re.escape(p.lower()) + r'"', text):
            raise InputRefused("a personal column name is in the model input")


# --- 3. Meaning (#120): direction and comparison words against the computed result --------------
#
# The numbers guard checks figures, not what a sentence concludes from them: "South is projected
# to lose ₹1.4 Cr while the West's gain will offset it, leaving the close unchanged" had the right
# figures and a conclusion the card never drew; "Three checks ran" when 3 were clean, 2 held back
# and 3 couldn't run. `contradiction` reads a sentence's direction words (rose, fell, up, down,
# higher, lower, above or below usual), its "unchanged" and "offset" claims, and its "N ran" and
# "total N" counts, and compares them with a `Computed` result built by code from what the
# sentence was written from (a tool card, a brief's items). Only clear contradictions: a clause
# that names one subject whose computed sign is the opposite; a claim of no change about a number
# that moved, or one the result never supports; a count that isn't the result's. A clause with a
# negation, a "could" or a plan or goal in it is not judged on direction. Names and computed
# results only, never rows.


@dataclass(frozen=True)
class Computed:
    """What a model sentence was written from, by code.

    signs: (subject as the result names it, +1 rose / -1 fell / 0 unchanged).
    total: how many in all, for a result that is a set of counts (the checks of a night).
    done: how many of them ran (the total less the ones that couldn't run).
    counts: the labelled counts ("Clean", 3), so "three ran clean" is right.
    steady, offset: the result itself says a number stayed the same, or parts offset each other.
    """

    signs: tuple[tuple[str, int], ...] = ()
    total: int | None = None
    done: int | None = None
    counts: tuple[tuple[str, int], ...] = ()
    steady: bool = False
    offset: bool = False


_UP = (
    r"rose|rise|rises|risen|rising|grew|grow|grows|grown|growing|growth|increase|increased|"
    r"increases|increasing|gain|gains|gained|gaining|climb|climbed|climbs|climbing|jump|jumped|"
    r"jumps|surge|surged|surges|up|higher"
)
_DOWN = (
    r"fell|fall|falls|fallen|falling|drop|dropped|drops|dropping|decline|declined|declines|"
    r"declining|decrease|decreased|decreases|decreasing|shrank|shrink|shrinks|shrunk|shrinking|"
    r"slipped|slip|slips|slid|dip|dipped|dips|lose|loses|lost|losing|loss|down|lower"
)
_REF = (
    r"(?:the\s+|its\s+|their\s+)?(?:usual|normal|average|last|previous|before|a year ago|"
    r"the month before|the week before)"
)
_WORDS_UP = re.compile(rf"\b(?:{_UP})\b|\b(?:above|more than|over)\s+{_REF}\b", re.IGNORECASE)
_WORDS_DOWN = re.compile(
    rf"\b(?:{_DOWN})\b|\b(?:below|less than|fewer than|under)\s+{_REF}\b", re.IGNORECASE
)
# "set up", "follow up", "up to you", "break down": not a direction
_NOT_UP = re.compile(
    r"\b(?:set|sets|pick|picks|picked|follow|follows|followed|make|makes|made|sign|signs|signed|"
    r"look|looks|looked|show|shows|showed|turn|turns|turned|come|comes|came|add|adds|added|end|"
    r"ends|ended|line|lines|lined|sum|sums|summed|bring|brings|brought|open|opens|opened|take|"
    r"takes|took|clean|cleans|cleaned|catch|catches|caught|keep|keeps|kept|back|backs|backed|"
    r"mix|mixed|top|topped|speed|sped)\s+up\b|\bup\s+(?:to|for|front)\b",
    re.IGNORECASE,
)
_NOT_DOWN = re.compile(
    r"\b(?:break|breaks|broke|broken|narrow|narrows|narrowed|track|tracks|tracked|shut|shuts|"
    r"write|writes|wrote|written|note|notes|noted|put|puts|let|lets|settle|settles|settled|step|"
    r"steps|stepped|hand|hands|handed|turn|turns|turned|drill|drills|drilled|scroll|further|"
    r"see|look)\s+down\b|\bdown\s*payment\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(?:not|no|never|neither|nor|without|cannot)\b|n't\b|\bno longer\b", re.IGNORECASE
)
# what may happen, or where a number stands against a plan, is not its movement
_HEDGE = re.compile(
    r"\b(?:could|may|might|would|will|should|if|unless|expect|expects|expected|projected|"
    r"projection|likely|forecast|forecasts|risk|chance|plan|plans|planned|target|targets|goal|"
    r"goals|budget|budgets)\b",
    re.IGNORECASE,
)
_STEADY = re.compile(
    r"\bunchanged\b|\bno (?:net )?change\b|\b(?:stayed|stays|stay|remained|remains|remain|held|"
    r"holds|hold|was|were|is|are|kept|keeps)\s+(?:flat|steady|level|stable|the same)\b|"
    r"\bflat\b(?!\s+(?:rate|rates|fee|fees|price|prices))|"
    r"\bcancel(?:s|led|ed|ling|ing)?\s+(?:\w+\s+){0,2}?out\b|"
    r"\bnet(?:s|ted)?\s+(?:out\s+)?to\s+(?:zero|nil|nothing)\b",
    re.IGNORECASE,
)
_NOT_STEADY = re.compile(
    r"\b(?:not|never)\s+(?:\w+\s+)?(?:unchanged|flat|steady|stable|the same)\b|"
    r"n't\s+(?:\w+\s+)?(?:unchanged|flat|steady|stable|the same)\b",
    re.IGNORECASE,
)
_STEADY_SUPPORT = re.compile(
    r"\bsame as\b|\bunchanged\b|\bno change\b|\bflat\b|\bsteady\b|\bstable\b", re.IGNORECASE
)
_OFFSET = re.compile(
    r"\boffset(?:s|ting)?\b|\b(?:make|makes|made|making)\s+up\s+for\b|"
    r"\bbalanc(?:e|es|ed|ing)\s+(?:\w+\s+)?out\b|\beven(?:s|ed)?\s+(?:\w+\s+)?out\b|"
    r"\bcompensat\w*",
    re.IGNORECASE,
)
_NOT_OFFSET = re.compile(r"\b(?:not|never)\s+(?:\w+\s+)?offset|n't\s+(?:\w+\s+)?offset", re.I)
_OFFSET_SUPPORT = re.compile(
    r"\boffset\w*|\bhid\b|\bhides?\b|\bhidden\b|\bhiding\b|\bmask\w*|\bcancel\w*|\bcompensat\w*",
    re.IGNORECASE,
)
_CLAUSES = re.compile(
    r"\s*[;:—–]\s*|,\s*|\s+(?:but|while|whereas|although|though|yet|and|as)\s+", re.IGNORECASE
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_RAN = re.compile(
    r"\b(?:ran|were run|was run|have run|had run|did run|got run|have been run|were checked)\b",
    re.IGNORECASE,
)
_TOTAL_AFTER = re.compile(
    r"\b(?:a total of|total of|totalling|totaling|in all,?|altogether,?)\s+([\w,-]+)",
    re.IGNORECASE,
)
_TOTAL_BEFORE = re.compile(r"\b(?:in total|in all|altogether)\b", re.IGNORECASE)
_NOT_RUN = re.compile(r"could ?n[o']t|not run|didn'?t run|skipped", re.IGNORECASE)
# a figure next to a direction word, as code writes a change ("down 4% on Sep 2026")
_MOVE = re.compile(
    rf"\b(?:{_UP}|{_DOWN})\s+(?:by\s+)?[+\-−]?₹?\s?\d|\d\s?%?\s+(?:{_UP}|{_DOWN})\b",
    re.IGNORECASE,
)


def _count(token: str) -> int | None:
    """A whole number written as digits or words ("3", "1,204", "three", "twenty-one")."""
    t = token.strip(",.;:").lower().replace(",", "")
    if t.isdigit():
        return int(t)
    if t in ("one", "zero"):
        return {"one": 1, "zero": 0}[t]
    got = _word_numbers(t) if _WORD.fullmatch(t) else set()
    return int(next(iter(got))) if len(got) == 1 else None


def _last_count(text: str) -> int | None:
    """The nearest whole number among the last three words of the text."""
    for w in reversed(re.findall(r"[\w,'-]+", text)[-3:]):
        if (n := _count(w)) is not None:
            return n
    return None


def _polarity(text: str) -> int | None:
    """+1 or -1 when the text's direction words all point one way; 0 when it has none; None
    when they disagree."""
    text = _NOT_DOWN.sub(" ", _NOT_UP.sub(" ", text))
    up, down = bool(_WORDS_UP.search(text)), bool(_WORDS_DOWN.search(text))
    if up and down:
        return None
    return 1 if up else -1 if down else 0


def _subjects(clause: str, signs: dict[str, int]) -> tuple[list[str], str]:
    """The result's subjects named in the clause (longest first), and the clause without them
    (a name with a direction word in it, "Customers lost", is not a direction)."""
    found = []
    rest = clause
    for name in sorted(signs, key=lambda n: (-len(n), n)):
        pat = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)
        if pat.search(rest):
            found.append(name)
            rest = pat.sub(" § ", rest)
    return found, rest


def _known(result: Computed) -> dict[str, int]:
    """Each subject's sign; a subject the result gives two signs is left out (not clear)."""
    out: dict[str, int] = {}
    bad: set[str] = set()
    for raw, sign in result.signs:
        name = raw.strip()
        if not name:
            continue
        if name in out and out[name] != sign:
            bad.add(name)
        out[name] = sign
    return {k: v for k, v in out.items() if k not in bad}


def _counts_wrong(sentence: str, result: Computed) -> str | None:
    """'ran' or 'total' when the sentence says how many ran, or how many in all, and it isn't
    the result's. "Three ran clean" is right when Clean is 3: the clause names that count."""
    text = sentence.replace("n't", " not")
    clauses = [c for c in _CLAUSES.split(text) if c and c.strip()]
    labelled = [(label.lower().replace("n't", " not"), n) for label, n in result.counts]

    def labelled_in(n: int, pos: int) -> bool:
        at = 0
        for c in clauses:
            at = text.find(c, at)
            if at <= pos <= at + len(c):
                return any(lb in c.lower() and v == n for lb, v in labelled)
            at += len(c)
        return False

    if result.done is not None:
        for m in _RAN.finditer(text):
            n = _last_count(text[: m.start()])
            if n is not None and n != result.done and not labelled_in(n, m.start()):
                return "ran"
    if result.total is not None:
        said = [_count(m.group(1)) for m in _TOTAL_AFTER.finditer(text)]
        said += [_last_count(text[: m.start()]) for m in _TOTAL_BEFORE.finditer(text)]
        if any(n is not None and n != result.total for n in said):
            return "total"
    return None


def contradiction(sentence: str, result: Computed) -> str | None:
    """Why the sentence contradicts the computed result ("direction", "steady", "offset", "ran",
    "total"), or None when it doesn't, or when that isn't clear."""
    if why := _counts_wrong(sentence, result):
        return why
    signs = _known(result)
    moved = {v for v in signs.values() if v}
    for clause in (c for c in _CLAUSES.split(sentence) if c and c.strip()):
        names, rest = _subjects(clause, signs)
        named = {signs[n] for n in names}
        offset_said = _OFFSET.search(rest) and not _NOT_OFFSET.search(rest)
        if offset_said and not (result.offset or {1, -1} <= moved):
            return "offset"  # parts offsetting each other, where none moved against another
        if _STEADY.search(rest) and not _NOT_STEADY.search(rest):
            if len(named) == 1 and 0 not in named:
                return "steady"  # a number that moved said to have stayed the same
            if not names and not (result.steady or 0 in signs.values()):
                return "steady"  # a conclusion the result never draws
        if len(named) != 1 or 0 in named:
            continue  # no subject, two with different signs, or one that didn't move
        if _NEGATION.search(clause) or _HEDGE.search(clause):
            continue
        said = _polarity(rest)
        if said and said == -next(iter(named)):
            return "direction"
    return None


def drop_contradictions(text: str, result: Computed) -> tuple[str, list[str]]:
    """The text without the sentences that contradict the result, and why each was dropped;
    the text unchanged when none does. Paragraphs and list lines stay."""
    why: list[str] = []
    lines = []
    for line in text.split("\n"):
        kept = []
        for sentence in _SENTENCE.split(line):
            got = contradiction(sentence, result) if sentence.strip() else None
            if got is None:
                kept.append(sentence)
            else:
                why.append(got)
        lines.append(" ".join(kept))
    if not why:
        return text, []
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(x.rstrip() for x in lines)).strip()
    return out, why


def supported(text: str) -> tuple[bool, bool]:
    """(steady, offset): whether the computed text itself says a number stayed the same, or
    that parts offset (or hid) each other."""
    return bool(_STEADY_SUPPORT.search(text)), bool(_OFFSET_SUPPORT.search(text))


def card_result(card: dict[str, Any]) -> Computed:
    """A tool card's computed result (`Card.as_json()`): a fact whose value states a change with
    its figure ("down 4% on Sep 2026") gives its label that sign, "same as" gives 0 (a card about
    one number, "Latest", gives the card's title); a card whose facts are all whole numbers is a
    set of counts: the total, and how many ran when some couldn't."""
    facts = [(str(f[0]), str(f[1])) for f in card.get("facts") or [] if len(f) == 2]
    title = str(card.get("title") or "")
    signs = []
    for label, value in facts:
        subject = title if label in ("Latest", "History") else label
        moves = " ".join(m.group(0) for m in _MOVE.finditer(value))
        pol = _polarity(moves) if moves else 0
        if pol:
            signs.append((subject, pol))
        elif not moves and re.search(r"\bsame as\b|\bunchanged\b|\bno change\b", value, re.I):
            signs.append((subject, 0))
    total = done = None
    counts: tuple[tuple[str, int], ...] = ()
    if len(facts) >= 2 and all(re.fullmatch(r"\d[\d,]*", v.strip()) for _, v in facts):
        counts = tuple((label, int(v.replace(",", ""))) for label, v in facts)
        total = sum(n for _, n in counts)
        not_run = [n for label, n in counts if _NOT_RUN.search(label)]
        done = total - sum(not_run) if not_run else None
    lines = [str(x) for x in card.get("lines") or []]
    steady, offset = supported(" ".join([title, *(f"{a} {b}" for a, b in facts), *lines]))
    return Computed(
        signs=tuple(signs), total=total, done=done, counts=counts, steady=steady, offset=offset
    )
