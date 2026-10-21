"""Download syllabi from Simple Syllabus to this computer.

    python -m reviewer.pull                          # ECEN syllabi awaiting approval, current + upcoming terms
    python -m reviewer.pull --term "Spring 2027"     # one term
    python -m reviewer.pull --status completed       # finished ones instead
    python -m reviewer.pull --courses 314 403        # only these course numbers
    python -m reviewer.pull --mine                   # only what's in your dashboard's To Do column (you press Enter when it has loaded)
    python -m reviewer.pull --mine --auto            # same, but picks Approvals itself and reads once loading stops
    python -m reviewer.pull --crns 56552 38440       # only these sections (the number in brackets)

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
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from . import dashboard
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


class PageRequest:
    """Makes API calls from inside the logged-in Simple Syllabus tab, exactly like the site's own
    page does (same browser, cookies and headers). Calls from a separate headless browser get
    blocked with 403 by the site's bot protection."""

    def __init__(self, page):
        self.page = page

    def get(self, url: str, timeout: int = 60_000):
        if not self.page.url.startswith(BASE_URL):
            self.page.goto(f"{BASE_URL}/en-US/dashboard", wait_until="domcontentloaded")
        res = self.page.evaluate(
            """async (url) => {
                 const r = await fetch(url, {credentials: "include", headers: {"Accept": "application/json"}});
                 return {status: r.status, text: await r.text()};
               }""", url)
        return _Resp(res.get("status", 0), res.get("text", ""))


class _Resp:
    def __init__(self, status: int, text: str):
        self.status, self._text = status, text

    def json(self):
        return json.loads(self._text)


class Stopped(Exception):
    """Stop was pressed in the web app's Simple Syllabus tab."""


def check_stop(stop):
    if stop is not None and stop.is_set():
        raise Stopped("Stopped.")


def login(pw, progress=say, stop=None):
    """Open a visible browser and wait for the reviewer to finish the TAMU login.
    Returns (context, PageRequest). The caller closes the context when the download is done."""
    ctx = launch(pw, headless=False, user_data_dir=str(prepare_profile()), viewport={"width": 1200, "height": 850})
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        dashboard.watch(page)        # keep the dashboard's own API answers from its very first load
        page.goto(f"{BASE_URL}/en-US/dashboard", wait_until="domcontentloaded")
        who = logged_in(ctx.request)
        if not who:
            progress("Log in to Simple Syllabus in the browser window that just opened (TAMU NetID + Duo). "
                     "Leave the window open; it closes by itself when the download is done.")
        deadline = time.time() + LOGIN_TIMEOUT_S
        while not who:
            check_stop(stop)
            if time.time() > deadline:
                raise SystemExit("Timed out waiting for login. Run it again when you're ready.")
            time.sleep(2)
            who = logged_in(ctx.request)
        progress(f"Logged in as {who}.")
        if not page.url.startswith(BASE_URL):
            page.goto(f"{BASE_URL}/en-US/dashboard", wait_until="domcontentloaded")
        return ctx, PageRequest(page)
    except BaseException:
        ctx.close()
        raise


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


def docs_for_cards(request, args, cards: list[dict], api_docs: dict) -> tuple[list[dict], list[str]]:
    """The syllabi for these To Do cards: Simple Syllabus's list (any status), else the dashboard's data."""
    keys = [dashboard.card_key(c["label"]) for c in cards]
    everything = []
    for s in sorted({k[0] for k in keys if k} | {args.subject}):
        everything += list_docs(request, SimpleNamespace(subject=s, status="all", term="", courses=[],
                                                         include_masters=False))
    docs, missing = dashboard.match_cards(cards, everything, api_docs)
    if args.term:
        docs = [d for d in docs if args.term.lower() in (d.get("term_name") or "").lower()]
    if args.courses:
        docs = [d for d in docs if str(d.get("course_number")) in args.courses]
    return docs, missing


def my_docs(request, args, out: Path, progress=say, stop=None, ready=None, on_seen=None,
            read: dict | None = None) -> list[dict]:
    """--mine / --crns: only the reviewer's own queue. Reads the To Do column off their dashboard
    (or takes the CRNs they typed), then finds those sections in the public list, whatever their status.
    ready: threading.Event the reviewer sets once the dashboard shows what they want (see dashboard.read_todo)."""
    crns = list(getattr(args, "crns", None) or [])
    check = {"mode": "crns" if crns else "dashboard"}
    api_docs = {}
    if crns:
        wanted, term = crns, args.term
    else:
        todo = dashboard.read_todo(request.page, progress, stop=stop, ready=ready, on_seen=on_seen,
                                   auto=getattr(args, "auto", False) or ready is None,
                                   ask=getattr(args, "ask", None) or "press Enter here")
        check_stop(stop)
        check.update({k: todo.get(k) for k in ("count", "capped", "tasks", "settled", "term", "toggle", "from_api",
                                               "problem", "api")})
        check["cards"] = todo["cards"]
        check["excluded"] = todo.get("excluded") or []
        if check["excluded"]:
            progress(f"Left out {len(check['excluded'])} card(s) that also show in another column: "
                     + ", ".join(f"{c['label']} ({c['also_in']})" for c in check["excluded"][:10]))
        cards, api_docs = todo["cards"], todo.get("api_docs") or {}
        if not cards:
            (out / "dashboard-check.json").write_text(json.dumps(check, indent=1), encoding="utf-8")
            if todo.get("problem"):
                # Never swap in a different list: the reviewer only cares about what's in To Do.
                raise SystemExit(f"Couldn't read your To Do column: {todo['problem']}")
            progress("Your To Do column is empty, so there's nothing to download.")
            return []
        count = todo.get("count")
        progress(f"Read {len(cards)} cards from your To Do column"
                 + (f" (it says {count}{'+' if todo.get('capped') else ''})" if count is not None else "")
                 + (f", {todo['from_api']} of them from the dashboard's own data" if todo.get("from_api") else "") + ".")
        if todo.get("toggle") in ("All", "Syllabus"):
            progress(f"Heads up: the dashboard was on {todo['toggle']}, not Approvals, so this includes "
                     "whatever that view lists in To Do.")
        if not todo.get("settled"):
            progress("Heads up: the To Do column was still changing when reading stopped.")
        if todo.get("tasks") and count is not None and count != todo["tasks"]:
            progress(f"Note: the welcome line says {todo['tasks']} tasks and the To Do column says {count}.")
        if count and not todo.get("capped") and len(cards) < count:
            progress(f"Heads up: only {len(cards)} of {count} cards could be read. The rest may still have been "
                     "loading. Check the list in dashboard-check.json, or run it again.")
        wanted = cards
        if read is not None:
            read["cards"] = cards
    if crns:
        everything = list_docs(request, SimpleNamespace(subject=args.subject, status="all", term="", courses=[],
                                                        include_masters=False))
        docs, missing = dashboard.match_crns(wanted, everything, term)
        if args.courses:
            docs = [d for d in docs if str(d.get("course_number")) in args.courses]
    else:
        docs, missing = docs_for_cards(request, args, wanted, api_docs)
    check.update({"wanted": wanted if crns else [c["label"] for c in wanted], "matched": len(docs), "missing": missing})
    (out / "dashboard-check.json").write_text(json.dumps(check, indent=1), encoding="utf-8")
    if missing:
        progress(f"Couldn't find these in Simple Syllabus ({len(missing)}): {', '.join(missing[:20])}")
    return sorted(docs, key=lambda d: (d.get("term_name") or "", d.get("title") or ""))


def enter_sets(event):
    """Each Enter on the keyboard sets the event (read_todo clears it when it starts waiting)."""
    while sys.stdin.readline():
        event.set()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download syllabi from Simple Syllabus for review.")
    ap.add_argument("--subject", default="ECEN", help="subject code (default ECEN)")
    ap.add_argument("--term", default="", help='part of the term name, e.g. "Spring 2027" (default: current + upcoming terms)')
    ap.add_argument("--status", default="awaiting_approval",
                    choices=["awaiting_approval", "in_progress", "completed", "not_started", "all"],
                    help="which syllabi to pull (default awaiting_approval = your To Do queue)")
    ap.add_argument("--courses", nargs="*", default=[], help="only these course numbers, e.g. 314 403")
    ap.add_argument("--mine", action="store_true", help="only the syllabi in your dashboard's To Do column")
    ap.add_argument("--everyone", dest="mine", action="store_false",
                    help="every syllabus with --status, not just your To Do column (undoes --mine)")
    ap.add_argument("--crns", nargs="*", default=[], help="only these sections, by CRN (the number in brackets)")
    ap.add_argument("--no-recheck", action="store_true",
                    help="with --mine: don't read the To Do column again at the end for new cards")
    ap.add_argument("--auto", action="store_true",
                    help="with --mine: don't wait for Enter, pick Approvals and read once the dashboard stops loading")
    ap.add_argument("--include-masters", action="store_true", help="also pull course-level master docs")
    ap.add_argument("--out", default="", help="output folder (default syllabi/<today>)")
    ap.add_argument("--no-pdf", action="store_true", help="skip PDFs, only save data for the audit")
    ap.add_argument("--logout", action="store_true", help="forget the saved TAMU login and stop")
    args = ap.parse_args(argv)
    if args.logout:
        say("Saved login removed." if forget_login() else "No saved login to remove.")
        return 0

    out = Path(args.out) if args.out else ROOT / "syllabi" / datetime.now().strftime("%Y-%m-%d")
    ready = None
    if args.mine and not args.crns and not args.auto and sys.stdin.isatty():
        ready = threading.Event()     # Enter = "the dashboard shows what I want"
        threading.Thread(target=enter_sets, args=(ready,), daemon=True).start()
    manifest = run_pull(args, out, ready=ready)
    blind = sum(1 for m in manifest if m["note"].startswith(("no content", "HTTP")))
    say(f"\nSaved to {out}")
    if blind:
        say(f"Note: {blind} {'syllabus' if blind == 1 else 'syllabi'} came back empty. "
            "Your account may not be allowed to see those drafts.")
    return 0


RECHECKS = 3


def recheck(request, args, out, cards, docs, download, total, progress, stop, on_seen, on_total) -> int:
    """After the downloads, read the To Do column again (no reload) and get any card that wasn't
    there the first time: cards that loaded late, or tasks added while this ran. Returns the new total."""
    have = {(c["label"], dashboard._norm_term(c["term"])) for c in cards}
    codes = {d.get("code") for d in docs}
    added = []
    for _ in range(RECHECKS):
        check_stop(stop)
        progress("Checking your To Do column again for anything that showed up while this ran...")
        todo = dashboard.read_todo(request.page, progress, stop=stop, on_seen=on_seen, recheck=True)
        check_stop(stop)
        new = [c for c in todo["cards"] if (c["label"], dashboard._norm_term(c["term"])) not in have]
        have |= {(c["label"], dashboard._norm_term(c["term"])) for c in new}
        more, missing = docs_for_cards(request, args, new, todo.get("api_docs") or {}) if new else ([], [])
        more = [d for d in more if d.get("code") not in codes]
        if not new:
            progress("Nothing new.")
            break
        progress(f"{len(new)} new in To Do: {', '.join(c['label'] for c in new[:20])}"
                 + (f". Couldn't find {', '.join(missing[:10])}" if missing else ""))
        codes |= {d.get("code") for d in more}
        added += [c["label"] for c in new]
        total += len(more)
        if on_total:
            on_total(total)
        download(more)
    f = out / "dashboard-check.json"
    if added and f.exists():
        check = json.loads(f.read_text(encoding="utf-8"))
        check["added_later"] = added
        f.write_text(json.dumps(check, indent=1), encoding="utf-8")
    return total


def run_pull(args, out: Path, progress=say, on_total=None, stop=None, ready=None, on_seen=None) -> list[dict]:
    """The whole download. Used by the command line and by the web app's Simple Syllabus tab.
    progress(msg) gets status lines; on_total(n) is called once the list is known.
    stop: a threading.Event; once it's set the run raises Stopped at the next safe point.
    ready / on_seen: for --mine, see my_docs and dashboard.read_todo."""
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    with sync_playwright() as pw:
        session, request = login(pw, progress, stop)
        browser = launch(pw, headless=True)          # only for making PDFs; never talks to Simple Syllabus
        try:
            read = {}
            if getattr(args, "mine", False) or getattr(args, "crns", None):
                docs = my_docs(request, args, out, progress, stop, ready, on_seen, read)
                progress(f"Found {len(docs)} syllabi to download.")
            else:
                docs = list_docs(request, args)
                progress(f"Found {len(docs)} {args.subject} syllabi ({args.status.replace('_', ' ')}"
                         f"{', ' + args.term if args.term else ''}).")
            check_stop(stop)
            if on_total:
                on_total(len(docs))
            page = render_page(browser)
            total = len(docs)

            def download(batch):
                for d in batch:
                    check_stop(stop)          # outside the try, so it isn't counted as one bad syllabus
                    try:
                        doc, status = fetch_doc(request, d)
                        entry = save_doc(doc, status, out, page, args.no_pdf)
                    except Exception as e:   # one bad syllabus never stops the run
                        entry = {"course": str(d.get("title") or d.get("code")), "code": d.get("code"), "pdf": None,
                                 "note": f"skipped: {type(e).__name__}: {str(e)[:80]}"}
                    manifest.append(entry)
                    progress(f"  [{len(manifest)}/{total}] {entry['course']}: {entry['note']}")
                    time.sleep(0.3)   # be gentle with their server
            download(docs)
            if read.get("cards") is not None and not getattr(args, "no_recheck", False):
                total = recheck(request, args, out, read["cards"], docs, download, total, progress, stop,
                                on_seen, on_total)
            (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        finally:
            browser.close()
            session.close()
    return manifest

if __name__ == "__main__":
    sys.exit(main())
