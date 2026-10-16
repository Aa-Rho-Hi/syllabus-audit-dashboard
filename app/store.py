"""Where faculty submissions live.

SheetStore: a Google Sheet (set SUBMISSIONS_SHEET to its link or ID and share it
            with the app's service account as Editor). Coordinators can also open it directly.
JsonStore:  a local file, used when no sheet is configured (local testing). On Cloud Run
            this file disappears on redeploy, so set a sheet there.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

log = logging.getLogger("store")

COLUMNS = ["id", "submitted_at", "status", "course_number", "course_name", "faculty_name",
           "faculty_email", "note", "objectives", "flagged", "issues", "ai_checked",
           "catalog_file", "catalog_objectives", "reviewer", "reviewer_comment", "reviewed_at"]
STATUSES = ("pending", "approved", "changes_requested")
TAB = "Submissions"


def _row_to_dict(row: list) -> dict:
    row = list(row) + [""] * (len(COLUMNS) - len(row))
    d = dict(zip(COLUMNS, row))
    d["objectives"] = [o for o in str(d["objectives"]).split("\n") if o.strip()]
    d["catalog_objectives"] = [o for o in str(d["catalog_objectives"]).split("\n") if o.strip()]
    d["issues"] = [o for o in str(d["issues"]).split("\n") if o.strip()]
    try:
        d["flagged"] = int(d["flagged"] or 0)
    except ValueError:
        d["flagged"] = 0
    d["ai_checked"] = str(d["ai_checked"]).upper() == "TRUE"
    return d


def _dict_to_row(d: dict) -> list:
    out = []
    for c in COLUMNS:
        v = d.get(c, "")
        if isinstance(v, list):
            v = "\n".join(v)
        elif isinstance(v, bool):
            v = "TRUE" if v else "FALSE"
        out.append("" if v is None else v)
    return out


class JsonStore:
    kind = "local file"

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return []

    def _save(self, rows: list[dict]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(rows, indent=1))

    def add(self, rec: dict):
        with self.lock:
            rows = self._load()
            rows.append(_dict_to_row(rec))
            self._save(rows)

    def all(self) -> list[dict]:
        return [_row_to_dict(r) for r in self._load()]

    def update(self, sid: str, changes: dict) -> dict | None:
        with self.lock:
            rows = self._load()
            for i, r in enumerate(rows):
                if r and r[0] == sid:
                    d = _row_to_dict(r)
                    d.update(changes)
                    rows[i] = _dict_to_row(d)
                    self._save(rows)
                    return d
        return None


class SheetStore:
    kind = "Google Sheet"

    def __init__(self, sheet_id: str):
        from googleapiclient.discovery import build
        from .sources import _service_account_creds
        creds = _service_account_creds(["https://www.googleapis.com/auth/spreadsheets"])
        if creds is None:
            raise RuntimeError("No server credentials for Google Sheets")
        self.sheet_id = sheet_id
        self.values = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets().values()
        self.meta = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()
        self.lock = threading.Lock()
        self._ensure_tab()

    def _ensure_tab(self):
        info = self.meta.get(spreadsheetId=self.sheet_id, fields="sheets.properties.title").execute()
        titles = [s["properties"]["title"] for s in info.get("sheets", [])]
        if TAB not in titles:
            self.meta.batchUpdate(spreadsheetId=self.sheet_id,
                                  body={"requests": [{"addSheet": {"properties": {"title": TAB}}}]}).execute()
        head = self.values.get(spreadsheetId=self.sheet_id, range=f"{TAB}!A1:Q1").execute().get("values", [])
        if not head:
            self.values.update(spreadsheetId=self.sheet_id, range=f"{TAB}!A1", valueInputOption="RAW",
                               body={"values": [COLUMNS]}).execute()

    def _rows(self) -> list[list]:
        return self.values.get(spreadsheetId=self.sheet_id, range=f"{TAB}!A2:Q").execute().get("values", [])

    def add(self, rec: dict):
        with self.lock:
            self.values.append(spreadsheetId=self.sheet_id, range=f"{TAB}!A1", valueInputOption="RAW",
                               insertDataOption="INSERT_ROWS", body={"values": [_dict_to_row(rec)]}).execute()

    def all(self) -> list[dict]:
        return [_row_to_dict(r) for r in self._rows() if r]

    def update(self, sid: str, changes: dict) -> dict | None:
        with self.lock:
            for i, r in enumerate(self._rows()):
                if r and r[0] == sid:
                    d = _row_to_dict(r)
                    d.update(changes)
                    self.values.update(spreadsheetId=self.sheet_id, range=f"{TAB}!A{i + 2}",
                                       valueInputOption="RAW", body={"values": [_dict_to_row(d)]}).execute()
                    return d
        return None


def parse_sheet_id(link: str) -> str | None:
    link = (link or "").strip()
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", link)
    if m:
        return m.group(1)
    return link if re.fullmatch(r"[A-Za-z0-9_-]{20,}", link) else None


_store = None
_store_error: str | None = None


def get_store(base: Path):
    """Sheet if configured and reachable, else local file. Never raises."""
    global _store, _store_error
    if _store is not None:
        return _store
    sid = parse_sheet_id(os.getenv("SUBMISSIONS_SHEET", ""))
    if sid:
        try:
            _store = SheetStore(sid)
            _store_error = None
            return _store
        except Exception as e:
            _store_error = (f"Can't open the submissions Google Sheet ({type(e).__name__}). "
                            "Share it with the app's service account as Editor.")
            log.error("%s %s", _store_error, e)
            return JsonStore(base / ".data" / "submissions.json")   # don't cache: retry the sheet next time
    _store = JsonStore(base / ".data" / "submissions.json")
    return _store


def store_status() -> str | None:
    return _store_error
