"""Where syllabi live: any Google Drive folder (by link) or a local folder (testing).

Drive access, tried in this order for each request:
  1. The professor's own Google sign-in token (reads anything they can see,
     including "Shared with me").
  2. A service account (folders shared with its email).
  3. An API key (folders set to "Anyone with the link").
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("sources")

SUPPORTED = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.google-apps.document": "gdoc",
    "text/plain": "txt",
    "text/markdown": "txt",
}
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"


class AccessError(Exception):
    def __init__(self, message: str, needs_signin: bool = False):
        super().__init__(message)
        self.needs_signin = needs_signin


# ---------------------------------------------------------------------------
def parse_folder_id(link: str) -> str | None:
    """Accepts a folder URL (any of Drive's formats) or a bare folder ID."""
    link = (link or "").strip()
    if not link:
        return None
    for pat in (r"/folders/([A-Za-z0-9_-]{10,})", r"[?&]id=([A-Za-z0-9_-]{10,})"):
        m = re.search(pat, link)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", link):
        return link
    return None


# ---------------------------------------------------------------------------
def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def docx_text(data: bytes) -> str:
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(parts)


def bytes_to_text(kind: str, data: bytes) -> str:
    if kind == "pdf":
        return pdf_text(data)
    if kind == "docx":
        return docx_text(data)
    return data.decode("utf-8", errors="ignore")


class TextCache:
    """Disk cache of extracted text keyed by file id + modified time.
    Only ever served for files that showed up in a listing made with the
    requester's own credentials."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data = json.loads(path.read_text())
        except Exception:
            self.data = {}

    def get(self, fid: str, modified: str) -> str | None:
        e = self.data.get(fid)
        return e["text"] if e and e.get("modified") == modified else None

    def put(self, fid: str, modified: str, text: str):
        with self.lock:
            self.data[fid] = {"modified": modified, "text": text}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data))
            except Exception as e:
                log.warning("cache write failed: %s", e)


# ---------------------------------------------------------------------------
class LocalSource:
    kind = "local"

    def __init__(self, root: str):
        self.root = Path(root)
        self.folder_id = f"local:{self.root.resolve()}"

    def check_access(self) -> str:
        if not self.root.is_dir():
            raise AccessError(f"Local folder not found: {self.root}")
        return self.root.name

    def list_files(self) -> list[dict]:
        out = []
        ext_kind = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".md": "txt"}
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and p.suffix.lower() in ext_kind and not p.name.startswith("~$"):
                out.append({
                    "id": f"{self.folder_id}/{p.relative_to(self.root)}", "name": p.name,
                    "kind": ext_kind[p.suffix.lower()],
                    "modified": str(int(p.stat().st_mtime)),
                    "path": "" if p.parent == self.root else str(p.parent.relative_to(self.root)),
                    "link": None, "_path": str(p),
                })
        return out

    def fetch_text(self, f: dict) -> str:
        return bytes_to_text(f["kind"], Path(f["_path"]).read_bytes())

    def fulltext_search(self, term: str, folder_ids: set[str]) -> set[str] | None:
        return None  # no server-side index; caller reads everything


class DriveSource:
    """Google's API client (httplib2) is NOT thread-safe: sharing one across threads can crash
    the whole process (signal 11). So every thread gets its own client, built on first use."""

    def __init__(self, folder_id: str, make_svc, kind: str):
        self.folder_id = folder_id
        self._make_svc = make_svc
        self._local = threading.local()
        self.kind = kind  # "user" | "service_account" | "api_key"

    @property
    def svc(self):
        svc = getattr(self._local, "svc", None)
        if svc is None:
            svc = self._local.svc = self._make_svc()
        return svc

    def check_access(self) -> str:
        """Cheap call that fails fast with 404/403 if these credentials can't see the folder."""
        meta = self.svc.files().get(fileId=self.folder_id, fields="id, name, mimeType",
                                    supportsAllDrives=True).execute()
        if meta.get("mimeType") != FOLDER_MIME:
            raise AccessError("That link points to a file, not a folder. Paste the folder link.")
        return meta.get("name", "")

    def _children(self, folder_id: str) -> list[dict]:
        items, token = [], None
        while True:
            resp = self.svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink, shortcutDetails)",
                pageSize=1000, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            items += resp.get("files", [])
            token = resp.get("nextPageToken")
            if not token:
                return items

    def list_files(self) -> list[dict]:
        out, stack, seen = [], [(self.folder_id, "")], set()
        while stack:
            fid, path = stack.pop()
            if fid in seen:
                continue
            seen.add(fid)
            for f in self._children(fid):
                mt, target = f["mimeType"], f["id"]
                if mt == SHORTCUT_MIME:  # follow shortcuts to their target
                    sd = f.get("shortcutDetails") or {}
                    mt, target = sd.get("targetMimeType", ""), sd.get("targetId", target)
                if mt == FOLDER_MIME:
                    stack.append((target, f"{path}/{f['name']}".strip("/")))
                elif mt in SUPPORTED:
                    out.append({
                        "id": target, "name": f["name"], "kind": SUPPORTED[mt],
                        "modified": f.get("modifiedTime", ""), "path": path,
                        "link": f.get("webViewLink"), "parent": fid,
                    })
        self.folder_ids = seen
        return out

    def fetch_text(self, f: dict) -> str:
        if f["kind"] == "gdoc":
            data = self.svc.files().export(fileId=f["id"], mimeType="text/plain").execute()
            return data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else str(data)
        data = self.svc.files().get_media(fileId=f["id"], supportsAllDrives=True).execute()
        return bytes_to_text(f["kind"], data)

    def fulltext_search(self, term: str, folder_ids: set[str]) -> set[str] | None:
        """Drive's own content index, limited to this folder tree."""
        if not folder_ids:
            return None
        safe = term.replace("\\", "").replace("'", "\\'")
        hits: set[str] = set()
        ids = list(folder_ids)
        try:
            for i in range(0, len(ids), 25):
                parents = " or ".join(f"'{p}' in parents" for p in ids[i:i + 25])
                resp = self.svc.files().list(
                    q=f"({parents}) and fullText contains '\"{safe}\"' and trashed = false",
                    fields="files(id)", pageSize=500,
                    supportsAllDrives=True, includeItemsFromAllDrives=True,
                ).execute()
                hits |= {f["id"] for f in resp.get("files", [])}
            return hits
        except Exception as e:
            log.warning("Drive fullText search failed: %s", e)
            return None


# ---------------------------------------------------------------------------
def _build(**kw):
    from googleapiclient.discovery import build
    return build("drive", "v3", cache_discovery=False, **kw)


DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def fixed_folder() -> str:
    """The department folder every request uses (faculty never paste a link)."""
    return (os.getenv("DRIVE_FOLDER_LINK") or os.getenv("DRIVE_FOLDER_ID") or "").strip() \
        if os.getenv("FIXED_FOLDER", "1") != "0" else ""


def _service_account_creds(scopes: list[str] | None = None):
    scopes = scopes or DRIVE_SCOPES
    from google.oauth2 import service_account
    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    creds_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_json:
        return service_account.Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    if creds_file and Path(creds_file).is_file():
        return service_account.Credentials.from_service_account_file(creds_file, scopes=scopes)
    # On Cloud Run (or after `gcloud auth application-default login`): use the
    # runtime identity. No key file needed; just share the folder with its email.
    if os.getenv("K_SERVICE") or os.getenv("USE_DEFAULT_CREDENTIALS") == "1":
        import google.auth
        creds, _ = google.auth.default(scopes=scopes)
        return creds
    return None


def server_identity() -> str | None:
    """Email the Drive folder must be shared with (shown to the admin in errors)."""
    if os.getenv("DRIVE_SHARE_WITH"):
        return os.getenv("DRIVE_SHARE_WITH")
    try:
        c = _service_account_creds()
        email = getattr(c, "service_account_email", None)
        if email and email != "default":
            return email
        if os.getenv("K_SERVICE"):
            import urllib.request
            req = urllib.request.Request(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email",
                headers={"Metadata-Flavor": "Google"})
            return urllib.request.urlopen(req, timeout=2).read().decode()
    except Exception:
        pass
    return None


def _http_status(e: Exception) -> int | None:
    return getattr(getattr(e, "resp", None), "status", None)


def resolve_source(folder_link: str, user_token: str | None, base_dir: Path):
    """Pick credentials that can actually see the folder. Returns (source, cache_key, folder_name)."""
    if fixed_folder():
        folder_link, user_token = fixed_folder(), None
    folder_id = parse_folder_id(folder_link)
    if not folder_link.strip():
        local = os.getenv("LOCAL_SYLLABI_DIR") or str(base_dir / "sample_syllabi")
        src = LocalSource(local)
        return src, src.folder_id, src.check_access()
    if not folder_id:
        raise AccessError("That doesn't look like a Google Drive folder link.")

    attempts: list[tuple[str, callable, str]] = []
    if user_token:
        def _user():
            from google.oauth2.credentials import Credentials
            return _build(credentials=Credentials(token=user_token))
        attempts.append(("user", _user, hashlib.sha256(user_token.encode()).hexdigest()[:16]))
    sa = None
    try:
        sa = _service_account_creds()
    except Exception as e:
        log.warning("service account key unreadable: %s", e)
    if sa:
        attempts.append(("service_account", lambda: _build(credentials=sa), "sa"))
    if os.getenv("GOOGLE_API_KEY"):
        attempts.append(("api_key", lambda: _build(developerKey=os.getenv("GOOGLE_API_KEY")), "key"))

    errors = []
    for kind, make, ckey in attempts:
        try:
            src = DriveSource(folder_id, make, kind)
            name = src.check_access()
            return src, f"{folder_id}:{ckey}", name
        except AccessError:
            raise
        except Exception as e:
            status = _http_status(e)
            errors.append(f"{kind}: {status or type(e).__name__}")
            if kind == "user" and status == 401:
                raise AccessError("Your Google sign-in expired. Sign in again.", needs_signin=True)
            log.info("access via %s failed: %s", kind, e)

    if fixed_folder():
        who = server_identity()
        raise AccessError("The app can't open the department Drive folder yet. The folder owner needs to share it"
                          + (f" with {who}" if who else " with the app's service account") + " as Viewer.")
    if not user_token:
        raise AccessError("Sign in with Google so the app can read this folder.", needs_signin=True)
    raise AccessError("Your Google account can't open that folder. Check the link, "
                      "or ask the owner to share it with you. (" + "; ".join(errors) + ")")


# ---------------------------------------------------------------------------
@dataclass
class FolderIndex:
    files: list[dict] = field(default_factory=list)
    folder_ids: set[str] = field(default_factory=set)
    listed_at: float = 0.0
    warming: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class Scanner:
    """One request's view of a folder: its credentials + shared listing/text caches."""

    _indexes: dict[str, FolderIndex] = {}
    _indexes_lock = threading.Lock()

    def __init__(self, source, cache_key: str, cache: TextCache, ttl: int = 300):
        self.source = source
        self.cache = cache
        self.ttl = ttl
        with Scanner._indexes_lock:
            self.index = Scanner._indexes.setdefault(cache_key, FolderIndex())

    def files(self, force: bool = False) -> list[dict]:
        idx = self.index
        with idx.lock:
            if force or not idx.listed_at or time.time() - idx.listed_at > self.ttl:
                idx.files = self.source.list_files()
                idx.folder_ids = getattr(self.source, "folder_ids", set())
                idx.listed_at = time.time()
        return idx.files

    def cached_text(self, f: dict) -> str | None:
        return self.cache.get(f["id"], f["modified"])

    def text(self, f: dict) -> str:
        cached = self.cached_text(f)
        if cached is not None:
            return cached
        try:
            t = self.source.fetch_text(f)
        except Exception as e:
            log.warning("could not read %s: %s", f["name"], e)
            return ""
        self.cache.put(f["id"], f["modified"], t)
        return t

    def fulltext_search(self, term: str) -> set[str] | None:
        return self.source.fulltext_search(term, self.index.folder_ids)

    def warm(self):
        """Read every file in the background so later lookups are instant."""
        idx = self.index
        if idx.warming:
            return
        idx.warming = True
        files = list(idx.files)

        def run():
            try:
                for f in files:
                    if self.cached_text(f) is None:
                        self.text(f)
                log.info("warm-up done: %d files", len(files))
            finally:
                idx.warming = False
        threading.Thread(target=run, daemon=True).start()
