"""Audit the syllabi that reviewer.pull downloaded and write the review Excel file.

    python -m reviewer.audit                 # newest folder under syllabi/
    python -m reviewer.audit syllabi/2026-10-18 --no-ai --open

Same checks as the web reviewer tab: approved Bloom's verbs, avoid-list words, suggested
rewrites, and objectives that overlap with another course. Uses AI only if LLM_API_KEY is in .env.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from app import llm                                   # noqa: E402
from app.audit_core import check_items                # noqa: E402
from app.overlap import find_overlaps                 # noqa: E402
from app.report import build_review_xlsx              # noqa: E402

from .syllabus_doc import course_label, doc_url, file_stem, find_outcomes, has_outcomes_section  # noqa: E402


def latest_folder() -> Path:
    runs = sorted(p for p in (ROOT / "syllabi").glob("*") if p.is_dir())
    if not runs:
        raise SystemExit("Nothing downloaded yet. Run:  python -m reviewer.pull")
    return runs[-1]


def audit_doc(doc: dict, json_path: Path, folder: Path, use_llm: bool) -> dict:
    pdf = json_path.parent.parent / f"{file_stem(doc)}.pdf"
    f = {"name": pdf.name if pdf.exists() else doc.get("title", ""),
         "path": doc.get("term_name", ""),
         # relative link for the web page (it turns it into its own file link)
         "link": pdf.relative_to(folder).as_posix() if pdf.exists() else doc_url(doc),
         # full path for Excel: still opens after "Download Excel" saves a copy in Downloads
         "excel_link": pdf.resolve().as_uri() if pdf.exists() else doc_url(doc)}
    base = {"file": f, "course": course_label(doc), "code": doc.get("code"), "url": doc.get("review_url") or doc_url(doc)}
    if not doc.get("components"):
        return {**base, "status": "unreadable", "objectives": [],
                "summary": "Couldn't see the content (your account may not have access to this draft)."}
    items, heading, renamed = find_outcomes(doc)
    res = {**base, **check_items(items, use_llm, "simple_syllabus", presplit=True)}
    if renamed and items:
        res["summary"] += f' Outcomes are under "{heading[:60]}" instead of the standard Course Learning Outcomes heading.'
        if res["status"] == "clean":
            res["status"] = "vague"           # still needs a look so it lands in the right place
    if not items:
        res["summary"] = ("Learning outcomes section is empty." if has_outcomes_section(doc)
                          else "No learning outcomes section in this syllabus.")
    if doc.get("is_missing_required_content") and doc.get("source") != "draft":   # drafts always say yes
        res["summary"] += " Simple Syllabus also flags missing required content."
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audit downloaded Simple Syllabus syllabi.")
    ap.add_argument("folder", nargs="?", help="folder from reviewer.pull (default: newest)")
    ap.add_argument("--no-ai", action="store_true", help="regex checks only")
    ap.add_argument("--open", action="store_true", help="open the Excel file when done")
    args = ap.parse_args(argv)

    folder = Path(args.folder) if args.folder else latest_folder()
    use_llm = not args.no_ai and llm.configured()
    results, overlaps, out = run_audit(folder, use_llm)
    need = sum(1 for r in results if r["status"] != "clean")
    print(f"\n{len(results) - need} look good, {need} need a look. Report: {out}")
    if args.open:
        try:
            if sys.platform == "win32":
                os.startfile(out)  # noqa: S606
            elif sys.platform == "darwin":
                os.system(f'open "{out}"')
        except Exception:
            pass
    return 0



def run_audit(folder: Path, use_llm: bool, progress=print):
    """Audit a download folder. Returns (results, overlaps, path of the Excel file)."""
    files = sorted(folder.glob("*/_data/*.json"))
    if not files:
        raise SystemExit(f"No downloaded syllabi in {folder}")
    progress(f"Auditing {len(files)} syllabi in {folder} (AI {'on' if use_llm else 'off'})...")
    results = []
    for p in files:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            r = audit_doc(doc, p, folder, use_llm)
        except Exception as e:  # one odd syllabus never stops the run
            r = {"file": {"name": p.stem, "link": None}, "course": p.stem,
                 "status": "unreadable", "objectives": [], "summary": f"Error: {e}"}
        results.append(r)
        progress(f"  {r['course']}: {r['summary']}")
    # sections of the same course share a syllabus, so only flag overlaps between different courses
    overlaps = find_overlaps(results, group=lambda r: " ".join(r["course"].split()[:2]))
    data = build_review_xlsx({"folder_name": f"Simple Syllabus download {folder.name}",
                              "results": results, "overlaps": overlaps})
    out = folder / f"syllabus-review-{datetime.now().strftime('%Y-%m-%d_%H%M')}.xlsx"
    out.write_bytes(data)
    (folder / "audit-results.json").write_text(json.dumps({"results": results, "overlaps": overlaps}, indent=1),
                                               encoding="utf-8")
    return results, overlaps, out


if __name__ == "__main__":
    sys.exit(main())
