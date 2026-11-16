# SPDX-License-Identifier: Apache-2.0
"""Money and times go through the one formatter (othor/core/fmt.py, M12b), so the v0.15 currency
setting and the viewer's and recipient's time zone change one module, not templates.

- No Python code outside fmt.py builds a "₹<number>" string (an f-string `₹{…}`).
- No template writes "₹{{ … }}" or formats a time with strftime or time_hm; times use
  fmt.clock().
Parsers that read "(₹ L)" from a sheet header are input, not display, and aren't matched."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "othor"
PY_MONEY = re.compile(r"f\"[^\"\n]*₹\{|f'[^'\n]*₹\{")
TPL_MONEY = re.compile(r"₹\{\{")
TPL_TIME = re.compile(r"strftime\([^)]*%H|fmt\.time_hm\(")


def test_no_hand_made_rupee_strings_in_code() -> None:
    bad = []
    for f in ROOT.rglob("*.py"):
        if f.name == "fmt.py":
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if PY_MONEY.search(line):
                bad.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()}")
    assert bad == [], "₹ outside fmt:\n" + "\n".join(bad)


def test_templates_use_the_formatters() -> None:
    bad = []
    for f in (ROOT / "web" / "templates").rglob("*.html"):
        if "components_demo" in f.parts:
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if TPL_MONEY.search(line) or TPL_TIME.search(line):
                bad.append(f"{f.relative_to(ROOT)}:{n}")
    for f in (ROOT / "mail" / "templates").rglob("*"):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if TPL_MONEY.search(line):
                bad.append(f"{f.relative_to(ROOT)}:{n}")
    assert bad == [], "format money and times with fmt:\n" + "\n".join(bad)


SCALING = re.compile(r"/\s*1e[57]\b|10\s*\*\*\s*[57]\b|\b10_000_000\b|\"10000000\"|\"100000\"")
# input parsers read "(₹ lakh)" from a sheet header: input, not display
INPUT_PARSERS = {"connectors/sheets/detect.py"}


def test_no_hand_made_lakh_or_crore_scaling() -> None:
    """M15.6: money is scaled by the company's style (fmt, finding_charts.money_unit), never by a
    fixed lakh or crore, so a USD company never sees crore-scaled numbers."""
    bad = []
    for f in ROOT.rglob("*.py"):
        rel = str(f.relative_to(ROOT))
        if f.name == "fmt.py" or rel in INPUT_PARSERS:
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if SCALING.search(line):
                bad.append(f"{rel}:{n}: {line.strip()}")
    assert bad == [], "scale money with fmt:\n" + "\n".join(bad)
