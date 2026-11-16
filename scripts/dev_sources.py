# SPDX-License-Identifier: Apache-2.0
"""Dev helper: on `make dev && make seed`, take the signed-in Indigo Thread owner to the Sources step
and connect the sample sources through the real HTTP flow (the worker container runs the jobs).

    uv run python scripts/dev_sources.py --cookie "$(uv run python scripts/dev_signin.py)"
    uv run python scripts/dev_sources.py --cookie ... --only mysql     # mysql · postgres · sheets
    uv run python scripts/dev_sources.py --cookie ... --writer         # shows the refusal

From inside compose the sample databases are erp-db:3306 and sales-dw:5432 (no SSL in dev).
Prints the source ids and the pages to open.
"""

from __future__ import annotations

import argparse
import re
import time

import httpx

APP = "http://localhost:8000"
HOST = "indigothread.othor.localhost"


def client(cookie: str) -> httpx.Client:
    name, _, value = cookie.strip().partition("=")
    c = httpx.Client(base_url=APP, headers={"Host": HOST}, follow_redirects=False, timeout=30)
    c.cookies.set(name, value)  # requests go to localhost:8000 with the company Host header
    return c


def csrf(c: httpx.Client, path: str) -> str:
    c.get(path)
    return str(c.cookies["othor_csrf"])


def to_sources(c: httpx.Client) -> None:
    for step in ("company", "upload", "business"):
        r = c.get(f"/setup/{step}")
        if r.status_code == 200:
            c.post(f"/setup/{step}/continue", data={"csrf": csrf(c, f"/setup/{step}")})


def wait(c: httpx.Client, path: str, done: str, seconds: int = 900) -> str:
    for _ in range(seconds):
        r = c.get(path)
        if done in r.text:
            return r.text
        time.sleep(1)
    raise SystemExit(f"timed out waiting for {done!r} on {path}")


def sql(c: httpx.Client, kind: str, user: str) -> str:
    form = {
        "postgres": {
            "host": "sales-dw",
            "port": "5432",
            "database": "sales_dw",
        },
        "mysql": {"host": "erp-db", "port": "3306", "database": "erp_db"},
    }[kind]
    tok = csrf(c, f"/setup/sources?panel=connect&kind={kind}")
    r = c.post(
        f"/sources/sql/{kind}/test",
        data={
            "csrf": tok,
            **form,
            "user": user,
            "password": f"{user}_dev",
            "ssl": "disable",
            "setup": "1",
        },
    )
    sid = re.search(r'name="sid" value="([0-9a-f-]{36})"', r.text)
    if not sid:
        raise SystemExit(r.text[:400])
    sid_s = sid.group(1)
    text = ""
    for _ in range(60):
        text = c.get(f"/sources/{sid_s}/test-status").text
        if "Testing the connection…" not in text:
            break
        time.sleep(1)
    print(f"{kind} as {user}: {re.sub(r'<[^>]+>', ' ', text).split()[:12]}")
    if "can change data" in text:
        return sid_s
    c.post(f"/sources/{sid_s}/connect", data={"csrf": tok})
    wait(c, f"/setup/sources?panel=scan&s={sid_s}", "Scanned ")
    print(f"  scanned: http://{HOST}:8000/setup/sources?panel=tables&s={sid_s}")
    return sid_s


def sheets(c: httpx.Client) -> str:
    tok = csrf(c, "/setup/sources/sheets")
    r = c.post("/sources/google/start", data={"csrf": tok, "setup": "1"})
    sid = r.headers["location"].split("sid=")[1]
    c.post(f"/sources/{sid}/files", data={"csrf": tok, "file": "sample-order-tracker-fy27"})
    text = wait(c, f"/setup/sources/sheets?sid={sid}", "What we found in each tab")
    print(f"  tabs: http://{HOST}:8000/setup/sources/sheets?sid={sid}")
    ids = re.findall(r'name="tab" value="([0-9a-f-]{36})"[^>]*checked', text)
    r = c.post(f"/sources/{sid}/tabs", data={"csrf": tok, "tab": ids})
    print(f"  check: http://{HOST}:8000{r.headers.get('location', '')}")
    return sid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookie", required=True)
    ap.add_argument("--only", choices=["mysql", "postgres", "sheets"])
    ap.add_argument("--writer", action="store_true", help="try othor_rw to see the refusal")
    a = ap.parse_args()
    c = client(a.cookie)
    to_sources(c)
    if a.writer:
        sql(c, "postgres", "othor_rw")
        return
    if a.only in (None, "mysql"):
        sql(c, "mysql", "othor_ro")
    if a.only in (None, "postgres"):
        sql(c, "postgres", "othor_ro")
    if a.only in (None, "sheets"):
        sheets(c)


if __name__ == "__main__":
    main()
