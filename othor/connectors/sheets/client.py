# SPDX-License-Identifier: Apache-2.0
"""Google Sheets access: the picked files only (Google Picker + `drive.file`, docs/05).

There is deliberately no call that lists or searches the user's Drive: the app sees exactly the
spreadsheets the owner picked. `FixtureSheetsClient` reads the local stand-in written by
`othor seed` (data/sample/sheets) and behaves like the API for tests and the demo.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from othor.core.http import outbound_client

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SCOPES = [DRIVE_FILE_SCOPE]  # never more (docs/05, docs/06 "Documents")
SHEETS_MIME = "application/vnd.google-apps.spreadsheet"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_API = "https://www.googleapis.com/drive/v3"


class TokenRevoked(RuntimeError):
    """Google access expired or was revoked: the source becomes disconnected (10m)."""


class AccessRemoved(RuntimeError):
    """This one picked file can no longer be read: "Access removed · Choose again"."""


@dataclass(frozen=True)
class FileMeta:
    id: str
    name: str
    modified: dt.datetime | None
    editor: str | None


class SheetsClient(Protocol):
    def account(self) -> str: ...
    def file_meta(self, file_id: str) -> FileMeta: ...
    def get(self, file_id: str) -> dict[str, Any]: ...


def _ts(v: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


class FixtureSheetsClient:
    """The stand-in. Revocation is read from auth.json (`revoked_at`); a removed file is one
    whose JSON is missing."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _auth(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self.folder / "auth.json").read_text())
        return data

    def _check(self) -> None:
        if self._auth().get("revoked_at"):
            raise TokenRevoked("Google sign-in was revoked or the password changed")

    def account(self) -> str:
        self._check()
        return str(self._auth()["account"])

    def picker_files(self) -> list[FileMeta]:
        """Only the fixture Picker uses this: the files a Picker dialog would offer."""
        index = json.loads((self.folder / "index.json").read_text())
        return [self.file_meta(f["spreadsheetId"]) for f in index]

    def _doc(self, file_id: str) -> dict[str, Any]:
        self._check()
        p = self.folder / f"{file_id}.json"
        if "/" in file_id or not p.exists():
            raise AccessRemoved(file_id)
        data: dict[str, Any] = json.loads(p.read_text())
        return data

    def file_meta(self, file_id: str) -> FileMeta:
        d = self._doc(file_id)
        drive = d.get("x_drive", {})
        return FileMeta(
            file_id,
            d["properties"]["title"],
            _ts(drive.get("modifiedTime")),
            (drive.get("lastModifyingUser") or {}).get("displayName"),
        )

    def get(self, file_id: str) -> dict[str, Any]:
        return self._doc(file_id)


class GoogleSheetsClient:
    """The real API with an access token for the `drive.file` scope."""

    def __init__(self, access_token: str, http: httpx.Client | None = None) -> None:
        self._token = access_token
        self._http = http or outbound_client()

    def _get(self, url: str, params: Any = None) -> dict[str, Any]:
        r = self._http.get(url, params=params, headers={"Authorization": f"Bearer {self._token}"})
        if r.status_code == 401:
            raise TokenRevoked("Google access expired or was revoked")
        if r.status_code in (403, 404):
            raise AccessRemoved(url)
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    def account(self) -> str:
        # about.get works under drive.file: no email or profile scope needed
        d = self._get(f"{DRIVE_API}/about", {"fields": "user(emailAddress)"})
        return str(d["user"]["emailAddress"])

    def file_meta(self, file_id: str) -> FileMeta:
        d = self._get(
            f"{DRIVE_API}/files/{quote(file_id)}",
            {"fields": "id,name,mimeType,modifiedTime,lastModifyingUser(displayName)"},
        )
        if d.get("mimeType") != SHEETS_MIME:
            raise AccessRemoved(file_id)  # only Google Sheets are ever read
        return FileMeta(
            file_id,
            d["name"],
            _ts(d.get("modifiedTime")),
            (d.get("lastModifyingUser") or {}).get("displayName"),
        )

    def get(self, file_id: str) -> dict[str, Any]:
        """Same shape as the stand-in: properties, merges and formatted values per sheet."""
        doc = self._get(
            f"{SHEETS_API}/{quote(file_id)}",
            {"fields": "spreadsheetId,properties(title,locale,timeZone),sheets(properties,merges)"},
        )
        titles = [s["properties"]["title"] for s in doc.get("sheets", [])]
        if titles:
            vals = self._get(
                f"{SHEETS_API}/{quote(file_id)}/values:batchGet",
                [("ranges", f"'{t.replace(chr(39), chr(39) * 2)}'") for t in titles]
                + [("valueRenderOption", "FORMATTED_VALUE"), ("majorDimension", "ROWS")],
            )
            for sheet, vr in zip(doc["sheets"], vals.get("valueRanges", []), strict=False):
                sheet["values"] = vr.get("values", [])
        meta = self.file_meta(file_id)
        doc["x_drive"] = {
            "modifiedTime": meta.modified.isoformat() if meta.modified else None,
            "lastModifyingUser": {"displayName": meta.editor},
        }
        return doc
