"""The reviewer's own queue: the "To Do" column on their Simple Syllabus dashboard.

The public list (doc-public) has every ECEN syllabus with a status, which is more than one
approver's queue (54 vs. Aydin's 31). The dashboard's own API (dashboard-doc-search) takes
parameters nobody has worked out yet, so instead this reads the To Do column off the page the
reviewer is already logged in to: each card shows "ECEN 314 200 (38440)". Those get matched to
the public list by CRN (the number in brackets), and the rest of the download works as before.

Page text is untrusted: it's only ever matched against CARD_RX (letters and digits), and only
the matched docs from Simple Syllabus's own list go on to be downloaded.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

CARD_RX = re.compile(r"\b([A-Z]{3,4})\s+(\d{3}[A-Z]?)\s+(\d{3}[A-Z]?)\s*\((\d{3,6})\)")

# Runs in the dashboard page. Finds the "To Do" column by its heading (no class names, they
# change), reads every card label in it, then scrolls it a bit so lazy-loaded cards show up.
SCRAPE_JS = r"""(opts) => {
  const RX = /\b([A-Z]{3,4})\s+(\d{3}[A-Z]?)\s+(\d{3}[A-Z]?)\s*\((\d{3,6})\)/g;
  const OTHER = /(^|\n)\s*(Not Ready|Completed|Awaiting Approval)\s*(\n|\d|$)/;
  const norm = e => (e.innerText || "").replace(/\s+/g, " ").trim();
  const short = (e, n) => (e.textContent || "").length < n;
  const heads = [...document.querySelectorAll("body *")].filter(e => short(e, 40) && /to ?do/i.test(e.textContent)
                                                                  && /^To ?Do( \d+\+?)?$/i.test(norm(e)));
  if (!heads.length) return {found: false};
  let head = heads[0];
  while (head.parentElement && /^To ?Do( \d+\+?)?$/i.test(norm(head.parentElement))) head = head.parentElement;
  const m = norm(head).match(/(\d+)(\+?)$/);
  let col = head;
  while (col.parentElement && col.parentElement !== document.body && !OTHER.test(col.parentElement.innerText || "")) col = col.parentElement;
  const labels = [...new Set([...(col.innerText || "").matchAll(RX)].map(x => `${x[1]} ${x[2]} ${x[3]} (${x[4]})`))];
  const more = [...col.querySelectorAll("button, a")].find(b => /^(load|show) more/i.test(norm(b)));
  const box = [col, ...col.querySelectorAll("*")].find(e => e.scrollHeight > e.clientHeight + 20 &&
                /(auto|scroll)/.test(getComputedStyle(e).overflowY));
  const at = () => box ? box.scrollTop + box.clientHeight >= box.scrollHeight - 5
                       : innerHeight + scrollY >= document.body.scrollHeight - 5;
  const bottom = at() && !more;          // read first, then move: lazy lists draw after the scroll event
  if (opts && opts.top) { if (box) box.scrollTop = 0; else window.scrollTo(0, 0); }
  else if (opts && opts.scroll) {
    if (more) more.click();
    else if (box) box.scrollTop += box.clientHeight * 0.6;
    else window.scrollBy(0, innerHeight * 0.6);
  }
  const termEl = [...document.querySelectorAll("label, legend, span, div")].find(e => short(e, 10) && norm(e) === "Term");
  let term = "";
  for (let p = termEl; p && !term && p !== document.body; p = p.parentElement) {
    const inp = p.querySelector("input");
    term = norm(p).replace(/^Term\s*/, "") || (inp && /[A-Za-z]/.test(inp.value) ? inp.value : "");
  }
  return {found: true, count: m ? +m[1] : null, capped: !!(m && m[2]), labels, bottom,
          term: term.slice(0, 80)};
}"""


def card_key(text: str) -> tuple | None:
    """("ECEN", "314", "200", "38440") from "ECEN 314 200 (38440)" or a doc title."""
    m = CARD_RX.search(text or "")
    return m.groups() if m else None


def parse_cards(text: str) -> list[str]:
    seen = []
    for m in CARD_RX.finditer(text or ""):
        label = f"{m[1]} {m[2]} {m[3]} ({m[4]})"
        if label not in seen:
            seen.append(label)
    return seen


def _term_matches(doc_term: str, dash_term: str) -> bool:
    """The dropdown can be cut off ("Spring 2027 - College ..."), so compare the season + year."""
    want = re.match(r"\s*(\w+\s+\d{4})", dash_term or "")
    return not want or (doc_term or "").lower().startswith(want[1].lower())


def match_docs(labels: list[str], docs: list[dict], term: str = "") -> tuple[list[dict], list[str]]:
    """Pick the docs for these card labels. Matches on subject, number, section and CRN."""
    by_key = {}
    for d in docs:
        k = card_key(d.get("title") or "")
        if k and d.get("entity_type", "section") == "section" and _term_matches(d.get("term_name"), term):
            by_key.setdefault(k, d)
    found, missing = [], []
    for label in labels:
        d = by_key.get(card_key(label))
        (found.append(d) if d else missing.append(label))
    return found, missing


def match_crns(crns: list[str], docs: list[dict], term: str = "") -> tuple[list[dict], list[str]]:
    want = [c for c in crns if c.isdigit()]
    found = [d for d in docs if (k := card_key(d.get("title") or "")) and k[3] in want
             and d.get("entity_type", "section") == "section" and (not term or term.lower() in (d.get("term_name") or "").lower())]
    have = {card_key(d["title"])[3] for d in found}
    return found, [c for c in want if c not in have]


def _shape(x, depth=0):
    """Structure of an API response with every value dropped (for fixing this later)."""
    if isinstance(x, dict):
        return {str(k)[:40]: _shape(v, depth + 1) for k, v in list(x.items())[:40]} if depth < 4 else "{...}"
    if isinstance(x, list):
        return [f"list[{len(x)}]", _shape(x[0], depth + 1)] if x else "list[0]"
    return type(x).__name__


def read_todo(page, progress=print, reload: bool = True, timeout_s: int = 45, stop=None) -> dict:
    """Read the To Do column of the dashboard that's open in `page`.
    Returns {"labels": [...], "count": 31, "capped": False, "term": "Spring 2027 - ...", "api": [...]}.
    labels is empty when the column couldn't be found. stop: a threading.Event that ends it early."""
    api = []

    def on_response(r):
        try:
            u = urlsplit(r.url)
            if "dashboard" in u.path and "/api" in u.path and len(api) < 20:
                api.append({"path": u.path[:80], "method": r.request.method, "status": r.status,
                            "params": sorted({p.split("=")[0][:40] for p in u.query.split("&") if p}),
                            "response": _shape(r.json())})
        except Exception:
            pass

    page.on("response", on_response)
    try:
        if reload:
            from .syllabus_doc import BASE_URL
            page.goto(f"{BASE_URL}/en-US/dashboard", wait_until="domcontentloaded")
        res = {"found": False}
        deadline = time.time() + timeout_s
        while time.time() < deadline and not (stop and stop.is_set()):
            res = page.evaluate(SCRAPE_JS, {})
            if res.get("found"):
                break
            time.sleep(1)
        if not res.get("found"):
            return {"labels": [], "count": None, "capped": False, "term": "", "api": api,
                    "problem": "Couldn't find the To Do column. Is the dashboard on View: Columns?"}
        try:   # the Approvals filter is what the reviewer uses; clicking it again does no harm
            page.get_by_text("Approvals", exact=True).first.click(timeout=3000)
            time.sleep(2)
        except Exception:
            pass
        page.evaluate(SCRAPE_JS, {"top": True})
        labels, still = [], 0
        for _ in range(200):
            if stop and stop.is_set():
                break
            time.sleep(0.5)
            res = page.evaluate(SCRAPE_JS, {"scroll": True})
            new = [x for x in parse_cards(" ".join(res.get("labels") or [])) if x not in labels]
            labels += new
            still = 0 if new else still + 1
            if res.get("count") is not None and len(labels) >= res["count"] and not res.get("capped"):
                break
            if still >= 4 and res.get("bottom"):
                break
        return {"labels": labels, "count": res.get("count"), "capped": res.get("capped", False),
                "term": res.get("term") or "", "api": api}
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass


def main() -> int:
    """python -m reviewer.dashboard: log in and show what's in the To Do column. Downloads nothing."""
    from playwright.sync_api import sync_playwright
    from . import pull
    with sync_playwright() as pw:
        session, request = pull.login(pw)
        try:
            todo = read_todo(request.page)
        finally:
            session.close()
    if not todo["labels"] and not todo.get("problem"):
        print(f"\nFound the To Do column on the {todo['term'] or 'selected'} dashboard. It's empty.")
        return 0
    if not todo["labels"]:
        print(f"\nCouldn't read the To Do column: {todo['problem']}")
        print("Dashboard API calls seen:", [a["path"] for a in todo["api"]] or "none")
        return 1
    print(f"\nTerm on the dashboard: {todo['term'] or '(not found)'}")
    print(f"To Do column says {todo['count']}{'+' if todo['capped'] else ''}, read {len(todo['labels'])}:")
    for label in todo["labels"]:
        print("  " + label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
