# SPDX-License-Identifier: Apache-2.0
"""Dev helper: create (or sign in to) a company through the real HTTP flow on `make dev`,
reading the email code from Mailpit. Prints `othor_session=<token>` for compare_screen --cookie.

    uv run python scripts/dev_signin.py                      # Indigo Thread, signs up if needed
    uv run python scripts/dev_signin.py --slug indigothread --email uk@indigothread.example
"""

from __future__ import annotations

import argparse
import re
import time

import httpx

APP = "http://localhost:8000"
MAILPIT = "http://localhost:8026"
BASE = "othor.localhost"


def latest_code(to: str, after: float) -> str:
    for _ in range(40):
        msgs = httpx.get(f"{MAILPIT}/api/v1/messages", params={"limit": 20}).json()["messages"]
        for m in msgs:
            if any(a["Address"] == to for a in m["To"]):
                created = m["Created"]
                ts = time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
                found = re.search(r"(\d{3}) (\d{3})", m["Subject"])
                if found and ts >= after - 5:
                    return found.group(1) + found.group(2)
        time.sleep(0.25)
    raise SystemExit(f"no code for {to} in Mailpit")


def client(host: str) -> httpx.Client:
    return httpx.Client(base_url=APP, headers={"Host": host}, follow_redirects=False, timeout=20)


def csrf(c: httpx.Client, path: str) -> str:
    c.get(path)
    return str(c.cookies["othor_csrf"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="indigothread")
    ap.add_argument("--email", default="uk@indigothread.example")
    ap.add_argument("--name", default="Uday Kamath")
    ap.add_argument("--company", default="Indigo Thread Co.")
    a = ap.parse_args()
    host = f"{a.slug}.{BASE}"
    c = client(host)
    if c.get("/login").status_code == 404:  # sign up on the root host
        r = client(BASE)
        tok = csrf(r, "/signup")
        t0 = time.time()
        r.post(
            "/signup", data={"csrf": tok, "name": a.name, "email": a.email, "company": a.company}
        )
        r.post("/signup/verify", data={"csrf": tok, "code": latest_code(a.email, t0)})
        res = r.post(
            "/signup/address",
            data={
                "csrf": tok,
                "slug": a.slug,
                "website": "https://indigothread.example",
                "fy": "4",
                "currency": "INR",
                "tz": "Asia/Kolkata",
            },
        )
        path = res.headers["location"].split(f"{host}:8000", 1)[-1].split(host, 1)[-1]
        res = c.get(path)
    else:  # sign in by code
        tok = csrf(c, "/login")
        t0 = time.time()
        c.post("/login", data={"csrf": tok, "email": a.email})
        res = c.post("/login/code", data={"csrf": tok, "code": latest_code(a.email, t0)})
    token = res.cookies.get("othor_session") or c.cookies.get("othor_session")
    print(f"othor_session={token}")


if __name__ == "__main__":
    main()
