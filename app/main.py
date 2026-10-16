from __future__ import annotations

import logging
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")

import os                                       # noqa: E402
import re                                       # noqa: E402
import time                                     # noqa: E402
from datetime import datetime                   # noqa: E402
from collections import Counter                 # noqa: E402

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, Response, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles     # noqa: E402
from pydantic import BaseModel                  # noqa: E402

from . import llm                                # noqa: E402
from .matcher import (name_score, number_regex,  # noqa: E402
                      parse_course_number, regex_extract_objectives)
from .sources import AccessError, Scanner, TextCache, fixed_folder, resolve_source  # noqa: E402
from .overlap import find_overlaps                  # noqa: E402
from .report import build_review_xlsx               # noqa: E402
from .verbs import (ALLOWED, AVOID_PHRASES, AVOID_VERBS, BLOOMS,  # noqa: E402
                    SUGGESTIONS, avoid_terms, clean_objective, final_status, regex_check,
                    split_objectives)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Syllabus Audit")
text_cache = TextCache(BASE / ".cache" / "text_cache.json")
pool = ThreadPoolExecutor(max_workers=8)

STRONG_NAME = 90
POSSIBLE_NAME = 70


class FolderIn(BaseModel):
    folder_link: str = ""


class LookupIn(FolderIn):
    course_number: str = ""
    course_name: str = ""


def _scanner(folder_link: str, token: str | None) -> tuple[Scanner, str, str]:
    src, key, name = resolve_source(folder_link, token or None, BASE)
    return Scanner(src, key, text_cache), name, src.kind


class CheckIn(BaseModel):
    text: str
    use_llm: bool = True


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/professor")
@app.get("/coordinator")
def role_page():
    """Same page; the path picks the view. "/" shows the role chooser."""
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/verbs")
def verbs():
    return {"blooms": BLOOMS, "avoid_verbs": AVOID_VERBS, "avoid_phrases": AVOID_PHRASES,
            "avoid_terms": avoid_terms(), "allowed": ALLOWED, "suggestions": SUGGESTIONS}


@app.get("/api/config")
def config():
    folder = os.getenv("DRIVE_FOLDER_LINK") or os.getenv("DRIVE_FOLDER_ID") or ""
    if folder and "/" not in folder:
        folder = f"https://drive.google.com/drive/folders/{folder}"
    return {
        "fixed_folder": bool(fixed_folder()),
        "google_client_id": None if fixed_folder() else (os.getenv("GOOGLE_CLIENT_ID") or None),
        "default_folder": folder,
        "llm_configured": llm.configured(),
        "llm_model": llm.MODEL if llm.configured() else None,
    }


@app.post("/api/folder")
def folder(body: FolderIn, x_google_token: str | None = Header(default=None)):
    """Connect to a folder: check access, list files, start reading them in the background."""
    try:
        sc, name, kind = _scanner(body.folder_link, x_google_token)
        files = sc.files(force=True)
    except AccessError as e:
        return {"ok": False, "message": str(e), "needs_signin": e.needs_signin}
    except Exception as e:
        return {"ok": False, "message": f"Couldn't read the folder: {type(e).__name__}: {str(e)[:200]}"}
    sc.warm()
    return {"ok": True, "folder_name": name, "access": kind, "files": len(files),
            "cached": sum(1 for f in files if sc.cached_text(f) is not None)}


def _objectives_for(text: str) -> dict:
    """Run regex and LLM extraction side by side; use whichever works."""
    fut = pool.submit(llm.extract_objectives, text) if llm.configured() else None
    rx = regex_extract_objectives(text)
    ai = fut.result() if fut else None
    if ai:
        return {"items": ai, "method": "llm", "regex_items": rx}
    if rx:
        return {"items": rx, "method": "regex", "regex_items": rx,
                "note": None if not llm.configured() else "AI extraction unavailable, used pattern matching"}
    return {"items": [], "method": "none",
            "note": "Couldn't find a learning objectives section in this syllabus."}


@app.post("/api/lookup")
def lookup(body: LookupIn, x_google_token: str | None = Header(default=None)):
    cn = parse_course_number(body.course_number)
    name = body.course_name.strip()
    try:
        library, folder_name, _ = _scanner(body.folder_link, x_google_token)
        files = library.files()
    except AccessError as e:
        return {"status": "error", "message": str(e), "needs_signin": e.needs_signin}
    except Exception as e:
        return {"status": "error", "message": f"Couldn't read the folder: {type(e).__name__}: {str(e)[:200]}"}

    num_re = number_regex(cn)

    # Narrow candidates: anything not yet cached is only fetched if its file name
    # or Drive's own full-text index points at it.
    drive_hits: set[str] = set()
    uncached = [f for f in files if library.cached_text(f) is None]
    if uncached:
        for term in filter(None, [cn.pretty if cn.num else None, name or None]):
            hits = library.fulltext_search(term)
            if hits is None:          # no server-side search (local mode) -> read everything
                drive_hits = {f["id"] for f in uncached}
                break
            drive_hits |= hits

    def consider(f):
        text = library.cached_text(f)
        if text is None:
            name_hit = (num_re and num_re.search(f["name"])) or (name and name_score(name, f["name"], "") >= POSSIBLE_NAME)
            if f["id"] in drive_hits or name_hit:
                text = library.text(f)
            else:
                return None
        num_hit = bool(num_re and (num_re.search(f["name"]) or num_re.search(text[:30000])))
        nscore = name_score(name, f["name"], text) if name else 0
        if not num_hit and nscore < POSSIBLE_NAME - 15:
            return None
        return {**f, "number_match": num_hit, "name_score": nscore, "header": text[:300]}

    cands = [c for c in pool.map(consider, files) if c]

    exact = [c for c in cands if c["number_match"]]
    named = [c for c in cands if not c["number_match"] and c["name_score"] >= STRONG_NAME]
    maybe = [c for c in cands if not c["number_match"] and POSSIBLE_NAME <= c["name_score"] < STRONG_NAME]

    ai_note = None
    if not exact and maybe and llm.configured():
        verdict = llm.same_course(name, cn.pretty, maybe[:10])
        if verdict:
            same = {maybe[i]["id"] for i in verdict.get("same", []) if isinstance(i, int) and i < len(maybe)}
            named += [c for c in maybe if c["id"] in same]
            maybe = [c for c in maybe if c["id"] not in same]
            ai_note = verdict.get("reason")

    def rank(c):
        return (c["number_match"], c["name_score"], c["modified"])

    if exact:
        status, best = "exists", sorted(exact, key=rank, reverse=True)
    elif named:
        status, best = "similar_name", sorted(named, key=rank, reverse=True)
    else:
        status, best = "not_found", sorted(maybe, key=rank, reverse=True)

    def pub(c):
        return {k: c[k] for k in ("id", "name", "path", "link", "modified", "number_match", "name_score")}

    out = {
        "status": status,
        "course": {"number": cn.pretty if cn.num else body.course_number, "name": name},
        "matches": [pub(c) for c in best[:8]],
        "ai_note": ai_note,
        "still_indexing": any(library.cached_text(f) is None for f in files),
        "folder_name": folder_name,
        "files_scanned": len(files),
    }
    if status in ("exists", "similar_name"):
        top = best[0]
        out["objectives"] = {**_objectives_for(library.text(top)), "file": pub(top)}
    if any(library.cached_text(f) is None for f in files):
        library.warm()
    return out


@app.post("/api/check")
def check(body: CheckIn):
    regex = regex_check(body.text)
    ai, ai_error = None, None
    if body.use_llm and llm.configured() and regex:
        ai = llm.check_objectives([r["text"] for r in regex])
        if ai is None:
            ai_error = llm.last_error or "AI check failed"
    elif body.use_llm and not llm.configured():
        ai_error = "No LLM key configured. Pattern check only."
    return {"objectives": split_objectives(body.text), "regex": regex, "llm": ai, "llm_error": ai_error}


# ---------------------------------------------------------------------------
# Whole-folder audit (runs in the background; the page polls for progress)
_COURSE_RE = re.compile(r"\b([A-Z]{2,5})[\s\-_]*(\d{3,4})\b")


_NOT_DEPT = {"TO", "OF", "AND", "THE", "FOR", "IN", "ON", "AT", "BY", "COPY", "FALL", "SPRING",
             "SUMMER", "WINTER", "VER", "REV", "PAGE", "ROOM", "SEC", "SECT", "LAB", "FINAL", "DRAFT"}


def _course_label(f: dict, text: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", f["name"])
    for src in (stem.upper(), text[:3000]):
        for m in _COURSE_RE.finditer(src):
            if m.group(1) not in _NOT_DEPT:
                return f"{m.group(1)} {m.group(2)}"
    return stem


def _audit_one(sc: Scanner, f: dict, use_llm: bool) -> dict:
    text = sc.text(f)
    base = {"file": {k: f.get(k) for k in ("id", "name", "path", "link")},
            "course": _course_label(f, text)}
    if not text.strip():
        return {**base, "status": "unreadable", "objectives": [], "summary": "Couldn't read this file."}
    items, method = regex_extract_objectives(text), "regex"
    if not items and use_llm:
        ai_items = llm.extract_objectives(text)
        if ai_items:
            items, method = ai_items, "llm"
    if not items:
        return {**base, "status": "no_objectives", "objectives": [],
                "summary": "No learning objectives section found."}

    rx = regex_check("\n".join(items))
    ai = llm.check_objectives([r["text"] for r in rx]) if use_llm and any(r["status"] != "ok" for r in rx) else None
    objs = []
    for r in rx:
        a = next((x for x in (ai or []) if x.get("index") == r["index"]), {})
        status = final_status(r, a)
        suggestion = a.get("suggestion") if status != "ok" else None
        if not suggestion and r["flags"] and r["leading_verb"] and r["flags"][0]["start"] == r["text"].find(r["leading_verb"]):
            sug = r["flags"][0]["suggest"]
            if sug:
                suggestion = sug[0] + r["text"][r["text"].find(r["leading_verb"]) + len(r["leading_verb"]):]
        objs.append({
            "text": r["text"], "status": status,
            "bloom_level": a.get("bloom_level") or (r["bloom_levels"] or [None])[0],
            "avoid_terms": [fl["base"] for fl in r["flags"]],
            "reasons": [i.get("reason") for i in (a.get("issues") or []) if i.get("reason")
                        and str(i.get("span", "")).lower() not in {fl["match"].lower() for fl in r["flags"]}],
            "suggestion": suggestion,
        })
    bad = [o for o in objs if o["status"] != "ok"]
    status = "clean" if not bad else ("avoid" if any(o["avoid_terms"] or o["status"] == "avoid" for o in bad) else "vague")
    return {**base, "status": status, "objectives": objs, "method": method,
            "summary": f"{len(bad)} of {len(objs)} objectives need changes" if bad else f"All {len(objs)} objectives look good"}


class AuditIn(FolderIn):
    use_llm: bool = True


def _summary(results: list[dict], overlaps: list[dict]) -> dict:
    counts = Counter(r["status"] for r in results)
    terms = Counter(t for r in results for o in r["objectives"] for t in o["avoid_terms"])
    return {"courses": len(results), "clean": counts["clean"], "avoid": counts["avoid"],
            "vague": counts["vague"], "no_objectives": counts["no_objectives"],
            "unreadable": counts["unreadable"],
            "objectives": sum(len(r["objectives"]) for r in results),
            "objectives_flagged": sum(1 for r in results for o in r["objectives"] if o["status"] != "ok"),
            "top_terms": terms.most_common(8), "overlap_pairs": len(overlaps),
            "overlap_courses": sum(1 for r in results if any(o.get("overlaps") for o in r["objectives"]))}


@app.post("/api/audit")
def audit(body: AuditIn, x_google_token: str | None = Header(default=None)):
    """Reviews the whole folder in ONE streaming request (one JSON object per line), so nothing
    is kept in server memory between requests and it works on any Cloud Run instance."""
    try:
        sc, folder_name, _ = _scanner(body.folder_link, x_google_token)
        files = sc.files()
    except AccessError as e:
        return {"ok": False, "message": str(e), "needs_signin": e.needs_signin}
    except Exception as e:
        return {"ok": False, "message": f"Couldn't read the folder: {type(e).__name__}: {str(e)[:200]}"}

    # reference docs in the folder (e.g. the Bloom's verb list) aren't courses
    files = [f for f in files if not re.search(r"bloom|taxonomy|verb.?list", f["name"], re.I)]
    use_llm = body.use_llm and llm.configured()

    def one(f):
        try:
            return _audit_one(sc, f, use_llm)
        except Exception as e:  # one bad file never kills the review
            return {"file": {"name": f["name"], "link": f.get("link"), "path": f.get("path")},
                    "course": f["name"], "status": "unreadable", "objectives": [], "summary": f"Error: {e}"}

    def stream():
        started = time.time()
        yield json.dumps({"type": "start", "ok": True, "folder_name": folder_name, "total": len(files),
                          "ai": use_llm}) + "\n"
        results = []
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(one, f) for f in files]
            for fut in as_completed(futures):
                r = fut.result()
                results.append(r)
                yield json.dumps({"type": "result", "result": r}) + "\n"
        try:
            overlaps = find_overlaps(results)       # also marks o["overlaps"] on each objective
        except Exception as e:
            log.warning("overlap check failed: %s", e)
            overlaps = []
        yield json.dumps({"type": "done", "results": results, "overlaps": overlaps,
                          "summary": _summary(results, overlaps),
                          "took": round(time.time() - started, 1)}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ExcelIn(BaseModel):
    folder_name: str = ""
    results: list[dict]
    overlaps: list[dict] = []


@app.post("/api/excel")
def excel(body: ExcelIn):
    """Coordinator's review file, built from the results the page already has. Nothing is saved."""
    if len(body.results) > 3000:
        raise HTTPException(413, "Too many courses for one file.")
    data = build_review_xlsx({"folder_name": body.folder_name, "results": body.results,
                              "overlaps": body.overlaps})
    name = re.sub(r"[^\w-]+", "_", body.folder_name or "folder")
    fname = f"syllabus-review-{name}-{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
