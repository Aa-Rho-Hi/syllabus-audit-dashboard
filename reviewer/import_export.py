"""Audit a Simple Syllabus export made in a browser (reviewer/export_snippet.js) instead of
running reviewer.pull. Useful when Python can't reach Simple Syllabus but a logged-in browser can.

    python -m reviewer.import_export simplesyllabus-ECEN-awaiting-approval.json
    python -m reviewer.audit syllabi/<today> --open

Uses the same safe save + PDF path as reviewer.pull (sanitised HTML, no JS, no outside network).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .pull import ROOT, render_page, save_doc, say
from .syllabus_doc import components_from_headings, slim


def doc_from_export(entry: dict) -> dict:
    listing = entry.get("listing") if isinstance(entry.get("listing"), dict) else {}
    raw = entry.get("doc") if isinstance(entry.get("doc"), dict) else {}
    doc = slim(raw)
    doc["code"] = doc.get("code") or listing.get("code")
    doc["title"] = doc.get("title") or listing.get("title") or ""
    doc["status"] = doc.get("status") or listing.get("status")
    doc["term_name"] = doc.get("term_name") or listing.get("term_name") or ""
    props = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
    props = {**{k: listing.get(k) for k in ("subject_name", "course_number") if listing.get(k)}, **props}
    headings = entry.get("headings") if isinstance(entry.get("headings"), list) else []
    if headings:
        doc["components"], doc["source"] = components_from_headings(headings, props), "draft"
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description="Turn a browser export into the folder reviewer.audit reads.")
    ap.add_argument("export", help="JSON file saved by reviewer/export_snippet.js")
    ap.add_argument("--out", default="", help="output folder (default syllabi/<today>)")
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.export).read_text(encoding="utf-8"))
    entries = [e for e in (data.get("docs") if isinstance(data, dict) else []) or [] if isinstance(e, dict)]
    out = Path(args.out) if args.out else ROOT / "syllabi" / datetime.now().strftime("%Y-%m-%d")
    out.mkdir(parents=True, exist_ok=True)

    page = browser = pw_ctx = None
    if not args.no_pdf:
        from playwright.sync_api import sync_playwright
        pw_ctx = sync_playwright().start()
        from .pull import launch
        browser = launch(pw_ctx, headless=True)
        page = render_page(browser)
    manifest = []
    try:
        for n, e in enumerate(entries, 1):
            try:
                entry = save_doc(doc_from_export(e), 200, out, page, args.no_pdf)
            except Exception as ex:      # one bad entry never stops the run
                entry = {"course": str((e.get("listing") or {}).get("title")), "pdf": None,
                         "note": f"skipped: {type(ex).__name__}: {str(ex)[:80]}"}
            say(f"  [{n}/{len(entries)}] {entry['course']}: {entry['note']}")
            manifest.append(entry)
    finally:
        if browser:
            browser.close()
        if pw_ctx:
            pw_ctx.stop()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    say(f"\nSaved {len(manifest)} syllabi to {out}. Next:  python -m reviewer.audit \"{out}\" --open")
    say("Delete the export file when you're done: it has instructor names and emails.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
