"""Download syllabi from Simple Syllabus to this computer.

    python -m reviewer.pull                          # ECEN syllabi awaiting approval, current + upcoming terms
    python -m reviewer.pull --term "Spring 2027"     # one term
    python -m reviewer.pull --status completed       # finished ones instead
    python -m reviewer.pull --courses 314 403        # only these course numbers

A browser window opens on Simple Syllabus. Log in with your TAMU NetID the normal way; the
script waits, then closes the window and does the rest in the background. Your password never
touches this script: it only reuses the login the browser already has.

Saves into syllabi/<date>/<term>/:  <course>.pdf for reading, and _data/<course>.json for the audit.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from .syllabus_doc import (BASE_URL, components_from_headings, course_label, doc_url, file_stem, has_outcomes_section,
                           objectives, printable_html, safe_folder, slim)

ROOT = Path(__file__).resolve().parent.parent
OLD_PROFILE_DIR = ROOT / ".ss-browser"   # older versions kept it next to the tool; removed on start
PROFILE_MAX_IDLE_DAYS = 14


def profile_dir() -> Path:
    """Where the browser remembers the TAMU login: the user's own private app-data folder, never
    the tool folder (which might get zipped, shared or synced)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "SyllabusAudit" / "browser"


def forget_login(path: Path | None = None) -> bool:
    path = path or profile_dir()
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
        return True
    return False


def prepare_profile(path: Path | None = None, now: float | None = None) -> Path:
    """Private folder, and a login that sat unused for 2 weeks is thrown away."""
    path = path or profile_dir()
    now = now or time.time()
    forget_login(OLD_PROFILE_DIR)
    stamp = path / ".last_used"
    if path.exists() and (not stamp.exists() or now - stamp.stat().st_mtime > PROFILE_MAX_IDLE_DAYS * 86400):
        forget_login(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)            # only this user (Windows app-data is already per-user)
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    stamp.touch()
    os.utime(stamp, (now, now))
    return path
LOGIN_TIMEOUT_S = 10 * 60
MAX_PAGES = 20                          # 20,000 syllabi; stops a runaway listing


def say(msg: str):
    print(msg, flush=True)


def launch(pw, headless: bool, **kw):
    """Use the Edge or Chrome already on the computer so nothing big has to be downloaded."""
    errors = []
    for channel in ("msedge", "chrome", None):
        try:
            if "user_data_dir" in kw:
                return pw.chromium.launch_persistent_context(channel=channel, headless=headless, **kw)
            return pw.chromium.launch(channel=channel, headless=headless)
        except Exception as e:  # browser not installed, try the next one
            errors.append(f"{channel or 'bundled chromium'}: {str(e).splitlines()[0]}")
    raise SystemExit("Couldn't start Edge or Chrome.\n  " + "\n  ".join(errors) +
                     "\nInstall Microsoft Edge or Google Chrome, or run:  python -m playwright install chromium")


def get_json(request, path: str, params: dict | None = None) -> tuple[int, dict]:
    url = f"{BASE_URL}{path}" + (f"?{urlencode(params, doseq=True)}" if params else "")
    r = request.get(url, timeout=60_000)
    try:
        return r.status, r.json()
    except Exception:
        return r.status, {}


def logged_in(request) -> str | None:
    status, j = get_json(request, "/api/session")
    acct = ((j or {}).get("data") or {}).get("account") or {}
    if status == 200 and acct.get("entity_id"):
        return f"{acct.get('first_name', '')} {acct.get('last_name', '')}".strip() or acct.get("email") or "you"
    return None


def login(pw) -> dict:
    """Open a visible browser, wait for the reviewer to finish TAMU login, hand back the session."""
    ctx = launch(pw, headless=False, user_data_dir=str(prepare_profile()), viewport={"width": 1200, "height": 850})
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{BASE_URL}/en-US/dashboard", wait_until="domcontentloaded")
        who = logged_in(ctx.request)
        if not who:
            say("\n>>> Log in to Simple Syllabus in the browser window that just opened (TAMU NetID + Duo).")
            say("    This window waits for you and closes by itself once you're in.\n")
        deadline = time.time() + LOGIN_TIMEOUT_S
        while not who:
            if time.time() > deadline:
                raise SystemExit("Timed out waiting for login. Run it again when you're ready.")
            time.sleep(2)
            who = logged_in(ctx.request)
        say(f"Logged in as {who}.")
        return ctx.storage_state()
    finally:
        ctx.close()


SAFE_IMAGE_PREFIX = f"{BASE_URL}/ui/account-image/"


def render_allowed(url: str, resource_type: str) -> bool:
    """Only inline data and Simple Syllabus logo/photo images. No ../ tricks out of that folder."""
    if url.startswith("data:image/"):
        return True
    return (resource_type == "image" and url.startswith(SAFE_IMAGE_PREFIX)
            and ".." not in url and "%2e" not in url.lower() and "?" not in url)


def render_page(browser):
    """A locked-down page for turning syllabus HTML into PDFs. The HTML is written by professors,
    so: no JavaScript, no login cookies, and no network except Simple Syllabus logo/photo images."""
    ctx = browser.new_context(java_script_enabled=False)

    def guard(route):
        if render_allowed(route.request.url, route.request.resource_type):
            return route.continue_()
        return route.abort()

    ctx.route("**/*", guard)
    return ctx.new_page()


def save_doc(doc: dict, status: int, out: Path, page, no_pdf: bool) -> dict:
    term_dir = out / safe_folder(doc.get("term_name") or "")
    (term_dir / "_data").mkdir(parents=True, exist_ok=True)
    stem = file_stem(doc)
    target = (term_dir / "_data" / f"{stem}.json").resolve()
    if out.resolve() not in target.parents:            # belt and braces against odd codes/titles
        raise ValueError("unsafe file name")
    target.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    pdf = None
    if not doc["components"]:
        note = "no content visible to your account" if status == 200 else f"HTTP {status}"
    else:
        n_obj = len(objectives(doc))
        note = f"{n_obj} objectives" if n_obj else ("objectives section empty" if has_outcomes_section(doc)
                                                   else "no objectives section")
        if not no_pdf and page is not None:
            pdf = term_dir / f"{stem}.pdf"
            try:
                page.set_content(printable_html(doc), wait_until="load", timeout=45_000)
                page.pdf(path=str(pdf), format="Letter", print_background=True,
                         margin={"top": "0.5in", "bottom": "0.6in", "left": "0.5in", "right": "0.5in"})
            except Exception as e:
                note += f"; PDF failed: {str(e).splitlines()[0][:80]}"
                pdf = None
    return {"course": course_label(doc), "title": doc.get("title"), "code": doc.get("code"),
            "term": doc.get("term_name"), "status": doc.get("status"), "url": doc_url(doc),
            "pdf": str(pdf.relative_to(out)) if pdf else None, "note": note}


def fetch_doc(request, d: dict) -> tuple[dict, int]:
    """One syllabus, as the reviewer would see it right now.
    Published ones come from doc-full-page-get (fully rendered). Drafts (awaiting approval,
    in progress) only exist in heading-component, so those get rebuilt from there."""
    code = d["code"]
    status, j = get_json(request, "/api2/doc-full-page-get", {"code": code})
    items = j.get("items") if isinstance(j, dict) else None
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    raw = first.get("doc_data") if isinstance(first.get("doc_data"), dict) else {}
    doc = slim(raw) if raw else {"code": code, "title": d.get("title") or "", "components": []}
    doc["term_name"] = doc.get("term_name") or d.get("term_name") or ""
    doc["status"] = doc.get("status") or d.get("status")
    doc["source"] = "published"
    entity, term = d.get("entity_id") or raw.get("entity_id"), d.get("term_id") or raw.get("term_id")
    if (doc["status"] != "completed" or not doc["components"]) and entity and term:
        hs, hj = get_json(request, "/api2/heading-component",
                          {"entity_id": entity, "term_id": term, "family_name": "syllabus"})
        props = {**{k: d.get(k) for k in ("subject_name", "course_number") if d.get(k)},
                 **(raw.get("properties") if isinstance(raw.get("properties"), dict) else {})}
        hitems = hj.get("items") if isinstance(hj, dict) else None
        comps = components_from_headings(hitems if isinstance(hitems, list) else [], props) if hs == 200 else []
        if comps:
            doc["components"], doc["source"] = comps, "draft"
        status = hs if not doc["components"] else 200
    return doc, status


def list_docs(request, args) -> list[dict]:
    params = {"subject_name": args.subject, "page_size": 1000}
    if args.status != "all":
        params["statuses[]"] = [args.status]
    if not args.term:
        params["term_statuses[]"] = ["current", "future"]
    docs, page = [], 0
    while True:
        status, j = get_json(request, "/api2/doc-public", {**params, "page": page})
        if status != 200 or not (j.get("sys") or {}).get("success"):
            raise SystemExit(f"Simple Syllabus list request failed (HTTP {status}): {json.dumps(j)[:300]}")
        items = j.get("items") or []
        docs += items
        total = (j.get("pagination") or {}).get("total") or 0
        page += 1
        if not items or page * 1000 >= total or page >= MAX_PAGES:
            break
    # The server filters are best-effort, so check again here.
    keep = []
    for d in docs:
        if d.get("entity_type") != "section" and not args.include_masters:
            continue
        if args.status != "all" and d.get("status") != args.status:
            continue
        if args.term and args.term.lower() not in (d.get("term_name") or "").lower():
            continue
        if args.courses and str(d.get("course_number")) not in args.courses:
            continue
        keep.append(d)
    return sorted(keep, key=lambda d: (d.get("term_name") or "", d.get("title") or ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download syllabi from Simple Syllabus for review.")
    ap.add_argument("--subject", default="ECEN", help="subject code (default ECEN)")
    ap.add_argument("--term", default="", help='part of the term name, e.g. "Spring 2027" (default: current + upcoming terms)')
    ap.add_argument("--status", default="awaiting_approval",
                    choices=["awaiting_approval", "in_progress", "completed", "not_started", "all"],
                    help="which syllabi to pull (default awaiting_approval = your To Do queue)")
    ap.add_argument("--courses", nargs="*", default=[], help="only these course numbers, e.g. 314 403")
    ap.add_argument("--include-masters", action="store_true", help="also pull course-level master docs")
    ap.add_argument("--out", default="", help="output folder (default syllabi/<today>)")
    ap.add_argument("--no-pdf", action="store_true", help="skip PDFs, only save data for the audit")
    ap.add_argument("--logout", action="store_true", help="forget the saved TAMU login and stop")
    args = ap.parse_args(argv)
    if args.logout:
        say("Saved login removed." if forget_login() else "No saved login to remove.")
        return 0

    from playwright.sync_api import sync_playwright

    out = Path(args.out) if args.out else ROOT / "syllabi" / datetime.now().strftime("%Y-%m-%d")
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        state = login(pw)
        browser = launch(pw, headless=True)
        ctx = browser.new_context(storage_state=state)
        try:
            docs = list_docs(ctx.request, args)
            say(f"Found {len(docs)} {args.subject} syllabi ({args.status.replace('_', ' ')}"
                f"{', ' + args.term if args.term else ''}).")
            if not docs:
                return 0
            page = render_page(browser)
            manifest = []
            for n, d in enumerate(docs, 1):
                try:
                    doc, status = fetch_doc(ctx.request, d)
                    entry = save_doc(doc, status, out, page, args.no_pdf)
                except Exception as e:   # one bad syllabus never stops the run
                    entry = {"course": str(d.get("title") or d.get("code")), "code": d.get("code"), "pdf": None,
                             "note": f"skipped: {type(e).__name__}: {str(e)[:80]}"}
                say(f"  [{n}/{len(docs)}] {entry['course']}: {entry['note']}")
                manifest.append(entry)
                time.sleep(0.3)   # be gentle with their server
            (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        finally:
            browser.close()

    blind = sum(1 for m in manifest if m["note"].startswith(("no content", "HTTP")))
    say(f"\nSaved to {out}")
    if blind:
        say(f"Note: {blind} {'syllabus' if blind == 1 else 'syllabi'} came back empty. "
            "Your account may not be allowed to see those drafts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
