# SPDX-License-Identifier: Apache-2.0
"""Licence check (decided 5 Nov 2026): every package shipped in the image, and every bundled
asset, has a permissive licence or a reviewed exception. CI fails otherwise.

    uv run python scripts/check_licences.py                 # check; exit 1 with the reasons
    uv run python scripts/check_licences.py --write OUT     # also write THIRD_PARTY_LICENSES

- Allowed without review: MIT, BSD-2/3-Clause, Apache-2.0, ISC, 0BSD, MIT-0, PSF-2.0, Zlib,
  CC0-1.0, Unlicense, the PostgreSQL License; OFL-1.1 for fonts and assets only.
- Reviewed exceptions: docs/licences/exceptions.yml (one package, a version range, its licence).
- Never allowed: GPL, AGPL, SSPL, BUSL, Commons Clause, or no licence found.

Only the runtime dependency closure of pyproject's `dependencies` counts (dev tools aren't in
the image). A licence is read from the SPDX expression, then the licence field, then the
classifiers, then the licence file's text.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
EXCEPTIONS = ROOT / "docs" / "licences" / "exceptions.yml"
ASSETS = ROOT / "docs" / "licences" / "assets.yml"

ALLOWED = {
    "MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "0BSD", "MIT-0", "PSF-2.0",
    "Zlib", "CC0-1.0", "Unlicense", "PostgreSQL",
    # MIT-CMU (Pillow): the historical PIL licence, permissive, the same freedoms as MIT (owner,
    # 13 Nov 2026, M15.7)
    "MIT-CMU",
}  # fmt: skip
ALLOWED_ASSETS = ALLOWED | {"OFL-1.1"}
NEVER = re.compile(r"\b(A?GPL|SSPL|BUSL|Commons[- ]Clause)", re.I)

CLASSIFIERS = {
    "MIT License": "MIT",
    "BSD License": "BSD-3-Clause",
    "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Zope Public License": "ZPL-2.1",
    "The Unlicense (Unlicense)": "Unlicense",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "GNU General Public License v2 or later (GPLv2+)": "GPL-2.0-or-later",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1-or-later",
    "Mozilla Public License 1.1 (MPL 1.1)": "MPL-1.1",
}
FIELD = {  # free-text licence fields seen in the wild → SPDX
    "mit": "MIT",
    "mit license": "MIT",
    "bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "isc license": "ISC",
    "isc": "ISC",
    "mpl-2.0": "MPL-2.0",
    "gplv2+": "GPL-2.0-or-later",
    "gplv3": "GPL-3.0-only",
}
TEXTS = (  # the licence file's own words, when the metadata says nothing
    ("Permission is hereby granted, free of charge", "MIT"),
    ("Apache License", "Apache-2.0"),
    ("Redistribution and use in source and binary forms", "BSD-3-Clause"),
    ("Permission to use, copy, modify, and/or distribute this software", "ISC"),
    ("GNU GENERAL PUBLIC LICENSE", "GPL"),
    ("GNU LESSER GENERAL PUBLIC LICENSE", "LGPL-3.0-only"),
    ("Mozilla Public License", "MPL-2.0"),
)


@dataclass
class Package:
    name: str
    version: str
    licence: str | None  # an SPDX expression, or None when nothing was found
    texts: list[str]


def _spdx_terms(expr: str) -> list[list[str]]:
    """'A OR (B AND C)' → [[A], [B, C]]: any alternative whose terms are all allowed passes."""
    expr = expr.replace("(", " ").replace(")", " ")
    return [
        [t.strip() for t in re.split(r"\s+AND\s+", alt) if t.strip()]
        for alt in re.split(r"\s+OR\s+", expr)
        if alt.strip()
    ]


def licence_of(meta: Any, texts: list[str]) -> str | None:
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr
    field = (meta.get("License") or "").strip()
    if field and len(field) < 60 and field.lower() in FIELD:
        return FIELD[field.lower()]
    # several licence classifiers are alternatives the package is offered under (pyphen: GPL,
    # LGPL or MPL), so they read as one "A OR B OR C" expression
    named = [
        CLASSIFIERS[c.split("::")[-1].strip()]
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::") and c.split("::")[-1].strip() in CLASSIFIERS
    ]
    if named:
        return " OR ".join(dict.fromkeys(named))
    for text in texts:
        for words, spdx in TEXTS:
            if words.lower() in text[:4000].lower():
                return spdx
    return None


def runtime_closure(pyproject: Path = ROOT / "pyproject.toml") -> list[Package]:
    deps = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    stack: list[tuple[str, frozenset[str]]] = []
    for d in deps:
        r = Requirement(d)
        stack.append((r.name, frozenset(r.extras)))
    seen: dict[str, Package] = {}
    while stack:
        name, extras = stack.pop()
        key = canonicalize_name(name)
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        if key not in seen:
            texts = [
                f.read_text(errors="replace")
                for f in (dist.locate_file(p) for p in dist.files or [])
                if re.search(r"(LICEN[CS]E|COPYING|NOTICE)", str(f).rsplit("/", 1)[-1], re.I)
                and Path(str(f)).is_file()
            ]
            seen[key] = Package(
                dist.metadata["Name"], dist.version, licence_of(dist.metadata, texts), texts
            )
        for r in dist.requires or []:
            req = Requirement(r)
            envs = [{"extra": e} for e in extras] or [{"extra": ""}]
            if req.marker and not any(req.marker.evaluate(env) for env in envs):
                continue
            stack.append((req.name, frozenset(req.extras)))
    return sorted(seen.values(), key=lambda p: canonicalize_name(p.name))


def load_yaml(path: Path) -> list[dict[str, Any]]:
    import yaml  # dev dependency (MIT): CI only, not in the image

    return list(yaml.safe_load(path.read_text()) or [])


def problems(
    packages: list[Package], exceptions: list[dict[str, Any]], assets: list[dict[str, Any]]
) -> list[str]:
    out: list[str] = []
    exc = {canonicalize_name(e["package"]): e for e in exceptions}
    for p in packages:
        key = canonicalize_name(p.name)
        if p.licence is None:
            out.append(f"{p.name} {p.version}: no licence found")
            continue
        if key in exc:
            e = exc[key]
            if p.version not in SpecifierSet(str(e["versions"])):
                out.append(
                    f"{p.name} {p.version}: outside its reviewed exception ({e['versions']})"
                )
            elif p.licence != e["licence"]:
                out.append(
                    f"{p.name} {p.version}: licence {p.licence} differs from the reviewed "
                    f"{e['licence']}"
                )
            continue
        if any(all(t in ALLOWED for t in alt) for alt in _spdx_terms(p.licence)):
            continue  # an alternative we can take entirely under allowed terms ("MIT OR GPL")
        if re.search(r"(^|[^L])\b(A?GPL|SSPL|BUSL|Commons[- ]Clause)", p.licence, re.I):
            out.append(f"{p.name} {p.version}: {p.licence} is never allowed")
        else:
            out.append(f"{p.name} {p.version}: {p.licence} isn't on the allowed list")
    for a in assets:
        lic = str(a.get("licence") or "")
        allowed = ALLOWED_ASSETS if a.get("kind") in ("font", "icons", "script") else ALLOWED
        if not lic:
            out.append(f"asset {a['name']}: no licence found")
        elif NEVER.search(lic) or lic not in allowed:
            out.append(f"asset {a['name']}: {lic} isn't allowed for a {a.get('kind')}")
    for key, e in exc.items():
        if key not in {canonicalize_name(p.name) for p in packages}:
            out.append(f"exception {e['package']} doesn't match an installed package (remove it)")
    return out


def third_party(packages: list[Package], assets: list[dict[str, Any]]) -> str:
    parts = [
        "Third-party licences in the Othor AI image (generated at build time).",
        "",
    ]
    for p in packages:
        parts += ["=" * 78, f"{p.name} {p.version} · {p.licence}", "=" * 78]
        parts += [t.strip() for t in p.texts] or ["(licence text not shipped by the package)"]
        parts.append("")
    for a in assets:
        version = f" {a['version']}" if a.get("version") else ""
        parts += ["=" * 78, f"{a['name']}{version} · {a['licence']} · {a.get('where', '')}", ""]
    return "\n".join(parts) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", type=Path, help="write THIRD_PARTY_LICENSES here")
    args = ap.parse_args()
    packages = runtime_closure()
    assets = load_yaml(ASSETS)
    bad = problems(packages, load_yaml(EXCEPTIONS), assets)
    for line in bad:
        print(f"licence: {line}", file=sys.stderr)
    if args.write:
        args.write.write_text(third_party(packages, assets))
    if bad:
        return 1
    print(f"{len(packages)} packages and {len(assets)} assets: licences allowed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
