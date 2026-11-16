# SPDX-License-Identifier: Apache-2.0
"""Build the stylesheets with the Tailwind v4 standalone CLI (no Node anywhere).

    uv run python scripts/build_css.py           # build every target: app.<sha8>.css (+ the
                                                 # website plug-in's site.<sha8>.css if present)
    uv run python scripts/build_css.py --watch   # dev: rebuild on change (make dev runs this)

Targets (M20.2): `app` (othor/web/styles/app.css → othor/web/static/css) and, when the private
website plug-in is in the tree, `site` (othor_site/styles/site.css → othor_site/static/site/css),
each with its own manifest.json.

The CLI is downloaded once per platform into .cache/ at a pinned version and verified against
the SHA-256 from that release's sha256sums.txt (copied below); a mismatch fails the build.
The output name carries a content hash; templates find it through static/css/manifest.json.
Used by `make css`, the Dockerfile and CI. Same inputs, same bytes, same name.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

VERSION = "v4.3.3"
SHA256 = {
    "linux-arm64": "55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195",
    "linux-x64": "dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a",
    "macos-arm64": "cdf646702987a743464dff4d9c60fd4480d1c1e73dd819a9a67f1078815dce9d",
    "macos-x64": "7922e0953f2110c05976e3bf58f14e643d90427575e766b7d433f5f80cbee7e1",
}
URL = "https://github.com/tailwindlabs/tailwindcss/releases/download/{v}/tailwindcss-{p}"

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("OTHOR_CSS_CACHE", ROOT / ".cache"))


@dataclass(frozen=True)
class Target:
    name: str  # the output's stem and manifest key: app → app.<sha8>.css
    source: Path
    out: Path

    @property
    def manifest(self) -> Path:
        return self.out / "manifest.json"


APP = Target(
    "app", ROOT / "othor" / "web" / "styles" / "app.css", ROOT / "othor" / "web" / "static" / "css"
)
SITE = Target(
    "site",
    ROOT / "othor_site" / "styles" / "site.css",
    ROOT / "othor_site" / "static" / "site" / "css",
)
TARGETS = [t for t in (APP, SITE) if t.source.exists()]
# kept for callers that read them (the app's stylesheet)
SOURCE, OUT, MANIFEST = APP.source, APP.out, APP.manifest


def current_platform() -> str:
    osname = {"darwin": "macos", "linux": "linux"}.get(sys.platform)
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine().lower()
    )
    if not osname or not arch:
        raise SystemExit(f"no Tailwind standalone build for {sys.platform}/{platform.machine()}")
    return f"{osname}-{arch}"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cli(plat: str | None = None) -> Path:
    """The verified binary, downloaded once."""
    plat = plat or current_platform()
    want = SHA256[plat]
    path = CACHE / f"tailwindcss-{VERSION}-{plat}"
    if not path.exists() or sha256(path) != want:
        CACHE.mkdir(parents=True, exist_ok=True)
        url = URL.format(v=VERSION, p=plat)
        print(f"downloading {url}", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        got = hashlib.sha256(data).hexdigest()
        if got != want:
            raise SystemExit(f"checksum mismatch for tailwindcss {VERSION} {plat}: {got}")
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        tmp.replace(path)
    return path


def compile_css(plat: str | None = None, t: Target = APP) -> bytes:
    binary = cli(plat)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / f"{t.name}.css"
        subprocess.run(  # noqa: S603 - our verified binary, fixed arguments
            [str(binary), "-i", str(t.source), "-o", str(tmp), "--minify"],
            check=True,
            cwd=ROOT,
            capture_output=True,
        )
        return tmp.read_bytes()


def shipped(t: Target = APP) -> Path | None:
    """The stylesheet the manifest points at, if both exist and it isn't empty."""
    if not t.manifest.exists():
        return None
    rel = json.loads(t.manifest.read_text()).get(f"{t.name}.css", "")
    path = t.out.parent / rel
    return path if rel and path.exists() and path.stat().st_size > 0 else None


def check(plat: str | None = None) -> int:
    """M12b: fail when a stylesheet is missing or stale (built from older templates or CSS).
    Rebuilds into memory and compares bytes with what each manifest points at."""
    return max(_check(plat, t) for t in TARGETS)


def _check(plat: str | None, t: Target) -> int:
    path = shipped(t)
    if path is None:
        print(
            "css: missing (run `make css`; the image builds it in its css stage)", file=sys.stderr
        )
        return 1
    if path.name == f"{t.name}.dev.css":
        print("css: the manifest points at the dev watch build; run `make css`", file=sys.stderr)
        return 1
    if compile_css(plat, t) != path.read_bytes():
        print(f"css: {path.name} is stale; run `make css`", file=sys.stderr)
        return 1
    print(f"css: {path.name} is current")
    return 0


def build(plat: str | None = None) -> Path:
    for t in TARGETS[1:]:
        _build(plat, t)
    return _build(plat, APP)


def _build(plat: str | None, t: Target) -> Path:
    data = compile_css(plat, t)
    name = f"{t.name}.{hashlib.sha256(data).hexdigest()[:8]}.css"
    t.out.mkdir(parents=True, exist_ok=True)
    for old in t.out.glob(f"{t.name}.*.css"):
        if old.name != name:
            old.unlink()
    (t.out / name).write_bytes(data)
    t.manifest.write_text(json.dumps({f"{t.name}.css": f"css/{name}"}, indent=2) + "\n")
    print(f"css/{name} ({len(data):,} bytes)")
    return t.out / name


def watch(plat: str | None = None) -> None:
    """Dev: fixed names (app.dev.css, site.dev.css) rebuilt on change; the manifests point at
    them. The site's watcher runs in the background beside the app's."""
    binary = cli(plat)
    procs = []
    for t in TARGETS:
        t.out.mkdir(parents=True, exist_ok=True)
        t.manifest.write_text(
            json.dumps({f"{t.name}.css": f"css/{t.name}.dev.css"}, indent=2) + "\n"
        )
        cmd = [
            str(binary),
            "-i",
            str(t.source),
            "-o",
            str(t.out / f"{t.name}.dev.css"),
            "--watch=always",
        ]
        procs.append(subprocess.Popen(cmd, cwd=ROOT))  # noqa: S603 - our verified binary
    for p in procs:
        p.wait()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--platform", choices=sorted(SHA256), help="default: this machine")
    ap.add_argument("--fetch-only", action="store_true", help="download and verify only")
    ap.add_argument(
        "--check", action="store_true", help="fail if the built CSS is missing or stale"
    )
    args = ap.parse_args()
    if args.check:
        raise SystemExit(check(args.platform))
    if args.fetch_only:
        print(cli(args.platform))
    elif args.watch:
        watch(args.platform)
    else:
        build(args.platform)


if __name__ == "__main__":
    main()
