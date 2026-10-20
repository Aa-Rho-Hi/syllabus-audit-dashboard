"""Runs the Simple Syllabus download + audit (reviewer/pull.py, reviewer/audit.py) as a background job
for the web app's "Simple Syllabus" tab.

Only works when the app runs on the reviewer's own computer: it opens a real browser window there
for the TAMU login. On Cloud Run (K_SERVICE is set) it's switched off.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

BASE = Path(__file__).resolve().parent.parent
SYLLABI = BASE / "syllabi"
STATUSES = ("mine", "awaiting_approval", "in_progress", "completed", "not_started", "all")


def available() -> tuple[bool, str]:
    if os.getenv("K_SERVICE"):
        return False, "Only works when the app runs on your own computer (it opens a browser for the TAMU login)."
    if os.getenv("SIMPLE_SYLLABUS_DISABLED"):
        return False, "Turned off on this server."
    try:
        import playwright  # noqa: F401
        import bs4  # noqa: F401
    except ImportError:
        return False, "Install the reviewer packages first:  pip install -r reviewer/requirements.txt"
    return True, ""


class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.state, self.message, self.log = "idle", "", []
        self.done = self.total = 0
        self.results, self.overlaps = None, None
        self.folder = self.excel = None
        self.started = None

    def snapshot(self) -> dict:
        with self.lock:
            return {"state": self.state, "message": self.message, "log": self.log[-60:],
                    "done": self.done, "total": self.total, "results": self.results, "overlaps": self.overlaps,
                    "folder": self.folder.name if self.folder else None,
                    "excel": file_url(self.folder, self.excel) if self.excel else None}

    def _log(self, msg: str):
        with self.lock:
            self.log.append(msg.strip())
            if msg.strip().startswith("[") and "/" in msg.split("]")[0]:
                self.done += 1
            if msg.startswith("Log in"):
                self.state = "login"
            elif msg.startswith("Logged in"):
                self.state = "downloading"

    def start(self, subject: str, term: str, status: str, courses: list[str], use_llm: bool) -> bool:
        with self.lock:
            if self.state in ("starting", "login", "downloading", "auditing"):
                return False
        self.reset()
        self.state, self.started = "starting", time.time()
        mine = status == "mine"           # the reviewer's own To Do column, read off their dashboard
        args = SimpleNamespace(subject=subject, term=term, status="awaiting_approval" if mine else status,
                               courses=courses, mine=mine, crns=[],
                               include_masters=False, no_pdf=False)
        threading.Thread(target=self._run, args=(args, use_llm), daemon=True).start()
        return True

    def _run(self, args, use_llm: bool):
        from reviewer.audit import run_audit
        from reviewer.pull import run_pull
        try:
            folder = SYLLABI / datetime.now().strftime("%Y-%m-%d_%H%M%S")
            self.folder = folder

            def on_total(n):
                with self.lock:
                    self.total, self.state = n, "downloading"
            manifest = run_pull(args, folder, progress=self._log, on_total=on_total)
            if not manifest:
                with self.lock:
                    self.state, self.message = "done", "No syllabi matched. Try another term or status."
                    self.results, self.overlaps = [], []
                return
            with self.lock:
                self.state = "auditing"
            results, overlaps, xlsx = run_audit(folder, use_llm, progress=self._log)
            for r in results:                     # links in the page open the downloaded PDF
                link = (r.get("file") or {}).get("link")
                if link and not link.startswith("http"):
                    r["file"]["link"] = file_url(folder, folder / link)
            with self.lock:
                self.results, self.overlaps, self.excel = results, overlaps, xlsx
                self.state = "done"
                self.message = f"Saved to {folder}"
        except BaseException as e:               # SystemExit from login timeout etc.
            with self.lock:
                self.state, self.message = "error", str(e) or type(e).__name__


JOB = Job()


def file_url(folder: Path, path: Path) -> str:
    rel = Path(path).resolve().relative_to(SYLLABI.resolve())
    return "/api/ss/file?path=" + quote(rel.as_posix())


def safe_file(rel: str) -> Path | None:
    """Only PDFs and Excel files inside syllabi/. Nothing else on the computer is reachable."""
    try:
        p = (SYLLABI / rel).resolve()
    except Exception:
        return None
    if SYLLABI.resolve() not in p.parents or p.suffix.lower() not in (".pdf", ".xlsx") or not p.is_file():
        return None
    return p


def open_folder(folder_name: str) -> bool:
    p = (SYLLABI / folder_name).resolve()
    if p.parent != SYLLABI.resolve() or not p.is_dir():
        return False
    cmd = {"darwin": ["open", str(p)], "win32": ["explorer", str(p)]}.get(sys.platform, ["xdg-open", str(p)])
    subprocess.Popen(cmd)                          # noqa: S603  (fixed program, our own folder path)
    return True
