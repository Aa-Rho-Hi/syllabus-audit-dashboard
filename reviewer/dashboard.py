"""The reviewer's own queue: the "To Do" column on their Simple Syllabus dashboard.

The public list (doc-public) has every ECEN syllabus with a status, which is more than one
approver's queue (54 vs. Aydin's 31). The dashboard's own API takes parameters nobody has worked
out yet, so instead this reads the To Do column off the page the reviewer is logged in to.

What Aydin's dashboard looks like (photos, 2026-10-20):
- Toggle All / Syllabus / Approvals. He uses Approvals (To Do 42; All shows 54).
- Term is a multi-select (Future: Spring 2027 College Station, Galveston, Qatar...; Current:
  Fall 2026 ...). The box only shows the first one, cut off: "Spring 2027 - College ...".
- Columns Not Ready 99+ / To Do 42 / Awaiting Approval 0 (collapsed, vertical) / Completed 99+.
- Each card: "Syllabus  Spring 2027 - College Station", "ECEN 322 200 (34317)", the title,
  a Review button, "✓ 1/2" and approver initials.
- Loading all the cards can take minutes; the count shows first, with a spinner.

So by default the reviewer sets the filters and says when it has loaded ("guided"). Then this
scrolls the whole column, keeps each card's own term (a CRN can come back in another term),
and checks the number read against the column's count. Cards it couldn't scroll to can be
filled in from the dashboard's own API responses, but only when one of them lists exactly that
many cards including every card that was read.

Page text and API responses are untrusted: labels only come out of CARD_RX (letters, digits),
terms are cut to plain characters, and API codes/IDs must look like codes/IDs.
"""
from __future__ import annotations

import re
import time
import weakref
from urllib.parse import urlsplit

CARD_RX = re.compile(r"\b([A-Z]{3,4})\s+(\d{3}[A-Z]?)\s+(\d{3}[A-Z]?)\s*\((\d{3,6})\)")
ID_RX = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
MAX_CARDS = 2000
OTHER_COLUMNS = {"notready": "Not Ready", "awaiting": "Awaiting Approval", "completed": "Completed"}
NOT_FOR_REVIEW = {"completed": "completed", "not_started": "not started"}   # Simple Syllabus statuses never in To Do

# Runs in the dashboard page. opts.scroll: "top", "down" or "nudge" (after reading).
SCRAPE_JS = r"""(opts) => {
  opts = opts || {};
  const RX = /\b([A-Z]{3,4})\s+(\d{3}[A-Z]?)\s+(\d{3}[A-Z]?)\s*\((\d{3,6})\)/g;
  const norm = s => (s || "").replace(/\s+/g, " ").trim();
  const txt = e => norm(e.innerText);
  const shown = e => e.getClientRects().length > 0;
  const labelsIn = s => [...new Set([...(s || "").matchAll(RX)].map(x => `${x[1]} ${x[2]} ${x[3]} (${x[4]})`))];

  // Which of All / Syllabus / Approvals is picked, if the page says so.
  let toggle = null;
  for (const b of document.querySelectorAll("button, a, label, li, [role=tab], [role=radio], [role=button]")) {
    const t = txt(b);
    if (!/^(All|Syllabus|Approvals)$/.test(t) || !shown(b)) continue;
    const cls = typeof b.className === "string" ? b.className : "";
    if (["aria-pressed", "aria-selected", "aria-checked"].some(a => b.getAttribute(a) === "true")
        || /(^|[\s_-])(active|selected|checked|on)(\b|_)/i.test(cls)) { toggle = t; break; }
  }
  // The Term box: first line after the word "Term" (the box only shows the first ticked term).
  let term = "";
  const termEl = [...document.querySelectorAll("label, legend, span, div, p")].find(e => (e.textContent || "").length < 10 && txt(e) === "Term");
  for (let p = termEl; p && !term && p !== document.body; p = p.parentElement) {
    const lines = (p.innerText || "").split("\n").map(norm).filter(Boolean);
    term = lines[0] === "Term" ? (lines[1] || "") : "";
    const inp = !term && p.querySelector("input");
    if (inp && /[A-Za-z]/.test(inp.value)) term = inp.value;
  }

  const tm = (document.body.innerText || "").match(/You have (\d+) tasks?/i), tasks = tm ? +tm[1] : null;
  // The four columns, by heading text. The real header sits level with the other headers, above
  // every card, so a card that happens to say "To Do" can't pass for one.
  const NAMES = {todo: "To ?Do", notready: "Not Ready", awaiting: "Awaiting Approval", completed: "Completed"};
  const headRx = k => new RegExp(`^${NAMES[k]}( \\d+\\+?)?$`, "i");
  const lineRx = k => new RegExp(`(^|\\n)\\s*${NAMES[k]}\\s*(\\n|\\d|$)`, k === "todo" ? "i" : "");
  const top = e => e.getBoundingClientRect().top + scrollY;
  const column = k => {
    const rx = headRx(k);
    const heads = [...document.querySelectorAll("body *")].filter(e => (e.textContent || "").length < 40 && shown(e) && rx.test(txt(e)));
    if (!heads.length) return null;
    heads.sort((a, b) => top(a) - top(b));
    let head = heads[0];
    while (head.parentElement && rx.test(txt(head.parentElement))) head = head.parentElement;
    // Grow to the column, stopping before the box that also holds another column's heading.
    const others = Object.keys(NAMES).filter(o => o !== k).map(lineRx);
    let col = head;
    while (col.parentElement && col.parentElement !== document.body && !others.some(r => r.test(col.parentElement.innerText || ""))) col = col.parentElement;
    return {head, col};
  };
  // Cards: the smallest elements holding one label, grown to the biggest box with only that label.
  const cardsIn = (col, limit, els) => {
    const small = [...col.querySelectorAll("*")].filter(e => (e.innerText || "").length < 90 && labelsIn(e.innerText).length === 1);
    const leaves = small.filter(e => ![...e.children].some(c => small.includes(c)));
    const cards = [], seen = new Set();
    for (const leaf of leaves) {
      const label = labelsIn(leaf.innerText)[0];
      // A title that looks like a label ("ECEN 999 999 (1)") sits under the real label in the same card.
      const near = leaf.parentElement && leaf.parentElement !== col ? labelsIn(leaf.parentElement.innerText) : [];
      if (near.length > 1 && near.length < 4 && near[0] !== label) continue;
      let card = leaf;
      const mine = e => { const ls = labelsIn(e.innerText); return ls.length === 1 || (ls.length < 4 && ls[0] === label && near.length > 1); };
      while (card.parentElement && card.parentElement !== col && mine(card.parentElement)) card = card.parentElement;
      const flat = norm(card.innerText), at = flat.search(/\b[A-Z]{3,4}\s+\d{3}[A-Z]?\s+\d{3}[A-Z]?\s*\(/);
      const before = at > 0 ? flat.slice(0, at) : "";
      const kind = (before.match(/^(Syllabus|Approval|Course)\b/i) || [""])[0];
      let cterm = norm(before.slice(kind.length));
      if (!cterm) cterm = (flat.match(/\b(Spring|Summer|Fall|Winter)\s+\d{4}\s*-\s*[A-Za-z ]{2,30}/) || [""])[0];
      const key = label + "|" + cterm;
      if (seen.has(key)) continue;
      seen.add(key);
      cards.push({label, term: cterm.slice(0, 80), kind});
      if (els) els.push(card);
      if (cards.length >= limit) break;
    }
    return cards;
  };

  const todo = column("todo");
  if (!todo) return {found: false, toggle, tasks, term: term.slice(0, 80)};
  const {head, col} = todo;
  const m = txt(head).match(/(\d+)(\+?)$/) || (head.parentElement && txt(head.parentElement).match(/^To ?Do\s+(\d+)(\+?)(\s|$)/i));
  const els = [], cards = cardsIn(col, 2000, els);
  // The same cards' neighbours: whatever the other columns show right now, to keep them out of To Do.
  const others = [];
  for (const k of ["notready", "awaiting", "completed"]) {
    const c = column(k);
    if (c && !c.col.contains(col) && !col.contains(c.col)) cardsIn(c.col, 300).forEach(x => others.push({...x, column: k}));
  }

  // Still loading? A spinner, a progress ring below 100%, or aria-busy in the header row or the column.
  let busy = false;
  for (const root of [head.parentElement || head, col]) {
    for (const e of [root, ...root.querySelectorAll("*")].slice(0, 4000)) {
      if (busy) break;
      if (!shown(e)) continue;
      if (e.getAttribute("aria-busy") === "true") busy = true;
      else if (e.getAttribute("role") === "progressbar") { const v = e.getAttribute("aria-valuenow"); busy = v === null || +v < 100; }
      else { const cs = getComputedStyle(e); busy = cs.animationName !== "none" && cs.animationIterationCount === "infinite" && cs.animationPlayState === "running"; }
    }
  }

  const more = [...col.querySelectorAll("button, a")].find(b => /^(load|show) more/i.test(txt(b)));
  // What actually scrolls the cards: the nearest scrolling box above the last card (the column's own
  // list, or an app box around the whole board, as on Aydin's), else the page.
  const scrollable = e => e && e !== document.body && e !== document.documentElement &&
    /(auto|scroll|overlay)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 20;
  const last = els.length ? els.reduce((a, b) => (a.compareDocumentPosition(b) & 4 ? b : a)) : col;
  let box = null;
  for (let p = last.parentElement; p && !box; p = p.parentElement) if (scrollable(p)) box = p;
  if (!box) box = [...col.querySelectorAll("*")].find(scrollable) || null;
  const sc = box || document.scrollingElement || document.documentElement;
  const chain = () => { let t = (document.scrollingElement || document.documentElement).scrollTop;
                        for (let p = last; p; p = p.parentElement) t += p.scrollTop || 0; return Math.round(t); };
  const bottom = sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 5 && !more;   // read first, then move
  const before = sc.scrollTop;
  if (opts.scroll === "top") { sc.scrollTop = 0; window.scrollTo(0, 0); }
  else if (opts.scroll === "down") { if (more) more.click(); else sc.scrollTop = before + sc.clientHeight * 0.6; }
  const wheel = dy => { for (const t of [last, sc]) t.dispatchEvent(new WheelEvent("wheel", {deltaY: dy, bubbles: true, cancelable: true})); };
  if (opts.scroll === "nudge") {    // lists that load at the bottom: bring the last card in, step back, go again
    wheel(-300); wheel(900);
    last.scrollIntoView({block: "end"});
    sc.scrollTop = Math.max(0, sc.scrollTop - sc.clientHeight);
    setTimeout(() => { sc.scrollTop = sc.scrollHeight; last.scrollIntoView({block: "end"}); }, 200);
  }
  const moved = sc.scrollTop !== before;
  if (opts.scroll === "down" && !moved && !more) wheel(Math.round((sc.clientHeight || innerHeight) * 0.6));
  // Where a mouse wheel would land inside the To Do column (for lists that only react to a real wheel).
  // Banners and pop-ups can sit on top of it, so only a spot where the column is really under the pointer.
  const cr = col.getBoundingClientRect();
  let wx = -1, wy = -1;
  const x0 = Math.max(cr.left, 0), x1 = Math.min(cr.right, innerWidth), y0 = Math.max(cr.top, 0), y1 = Math.min(cr.bottom, innerHeight);
  for (let y = y1 - 20; wx < 0 && y > y0; y -= 40)
    for (const x of [(x0 + x1) / 2, x0 + 20, x1 - 20]) {
      const hit = x > 0 && x < innerWidth ? document.elementFromPoint(x, y) : null;
      if (hit && col.contains(hit)) { wx = Math.round(x); wy = Math.round(y); break; }
    }
  return {found: true, count: m ? +m[1] : null, capped: !!(m && m[2]), cards, others, bottom, moved, pos: chain(),
          wx, wy, view: sc.clientHeight || innerHeight, busy, tasks, toggle, term: term.slice(0, 80)};
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


def clean_term(t) -> str:
    """Plain text only (letters, digits, spaces, & - and the ... of a cut-off term)."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 &.\-…]", " ", str(t or ""))).strip()[:80]


def _norm_term(t: str) -> str:
    return re.sub(r"\s+", " ", clean_term(t).lower().rstrip(".… ")).strip()


def _term_matches(doc_term: str, dash_term: str) -> bool:
    """The dropdown can be cut off ("Spring 2027 - College ..."), so compare the season + year."""
    want = re.match(r"\s*(\w+\s+\d{4})", dash_term or "")
    return not want or (doc_term or "").lower().startswith(want[1].lower())


def _same_term(doc_term: str, card_term: str) -> bool:
    a, b = _norm_term(doc_term), _norm_term(card_term)
    return not a or not b or a == b or a.startswith(b) or _term_matches(doc_term, card_term)


def _pick(cands: list[dict], term: str) -> dict | None:
    """The one doc for a card. Same section + CRN can exist in two terms, so use the card's term,
    and never take a doc from a different term, even when it's the only one."""
    cands = [d for d in cands if _same_term(d.get("term_name"), term)]
    if len(cands) <= 1:
        return cands[0] if cands else None
    want = _norm_term(term)
    if not want:
        return None
    for test in (lambda d: _norm_term(d.get("term_name")) == want,
                 lambda d: _norm_term(d.get("term_name")).startswith(want),
                 lambda d: _term_matches(d.get("term_name"), term)):
        hits = [d for d in cands if test(d)]
        if len(hits) == 1:
            return hits[0]
    return None


def match_cards(cards: list[dict], docs: list[dict], api_docs: dict | None = None) -> tuple[list[dict], list[str]]:
    """Pick the doc for each card, from Simple Syllabus's list first, else from the dashboard's own data."""
    by_key = {}
    for d in docs:
        k = card_key(d.get("title") or "")
        if k and d.get("entity_type", "section") == "section":
            by_key.setdefault(k, []).append(d)
    found, missing, used = [], [], set()
    for c in cards:
        k = card_key(c.get("label") or "")
        if not k:
            continue
        name = c["label"] + (f" ({clean_term(c['term'])})" if c.get("term") else "")
        d = _pick(by_key.get(k) or (api_docs or {}).get(c["label"]) or [], c.get("term") or "")
        if d is not None and d.get("status") in NOT_FOR_REVIEW:
            missing.append(f"{name}: Simple Syllabus lists it as {NOT_FOR_REVIEW[d['status']]}, so it's left out")
            continue
        if d is None or id(d) in used:
            if d is None:
                missing.append(name)
            continue
        used.add(id(d))
        found.append(d)
    return found, missing


def match_docs(labels: list[str], docs: list[dict], term: str = "") -> tuple[list[dict], list[str]]:
    """Labels without their own terms, all from the dashboard term `term`."""
    return match_cards([{"label": x, "term": term} for x in labels], docs)


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


def api_doc(x) -> dict | None:
    """A doc out of a dashboard API response, keeping only safe-looking fields. None if it isn't one."""
    if not isinstance(x, dict):
        return None
    labels = parse_cards(x.get("title") if isinstance(x.get("title"), str) else "")
    code = str(x.get("code") or "")
    if len(labels) != 1 or not ID_RX.match(code) or x.get("entity_type", "section") != "section":
        return None
    k = card_key(labels[0])
    d = {"title": labels[0], "code": code, "entity_type": "section", "subject_name": k[0], "course_number": k[1],
         "term_name": clean_term(x.get("term_name")), "source_hint": "dashboard"}
    for f in ("entity_id", "term_id"):
        if ID_RX.match(str(x.get(f) or "")):
            d[f] = x[f]
    if re.match(r"^[a-z_]{1,30}$", str(x.get("status") or "")):
        d["status"] = x["status"]
    return d


def api_lists(payloads: list) -> list[list[dict]]:
    """Every list of docs found anywhere in these JSON payloads (walks at most 20k nodes each)."""
    out = []
    for p in payloads:
        stack, n = [p], 0
        while stack and n < 20_000:
            x = stack.pop()
            n += 1
            if isinstance(x, list):
                ds = [d for d in (api_doc(i) for i in x[:MAX_CARDS]) if d]
                if ds:
                    out.append(ds)
                stack += [i for i in x[:MAX_CARDS] if isinstance(i, (list, dict))]
            elif isinstance(x, dict):
                stack += [v for v in list(x.values())[:200] if isinstance(v, (list, dict))]
    return out


_WATCHED = weakref.WeakKeyDictionary()
LONG_REQUEST_S = 60        # a request open longer than this is a long-poll, not loading


class _Watch:
    """The page's API responses (for filling in cards) and its open requests (still loading?)."""

    def __init__(self, page):
        self.seen, self.open = [], {}
        page.on("response", self.on_response)
        page.on("request", self.on_request)
        page.on("requestfinished", self.on_done)
        page.on("requestfailed", self.on_done)

    def on_response(self, r):
        try:
            if len(self.seen) < 400 and "/api" in urlsplit(r.url).path and r.request.resource_type in ("xhr", "fetch"):
                self.seen.append(r)
        except Exception:
            pass

    def on_request(self, r):
        try:
            if r.resource_type in ("xhr", "fetch") and len(self.open) < 1000:
                self.open[r] = time.time()
        except Exception:
            pass

    def on_done(self, r):
        self.open.pop(r, None)

    def busy(self) -> bool:
        cutoff = time.time() - LONG_REQUEST_S
        return any(t > cutoff for t in list(self.open.values()))

    def close(self, page):
        for ev, fn in (("response", self.on_response), ("request", self.on_request),
                       ("requestfinished", self.on_done), ("requestfailed", self.on_done)):
            try:
                page.remove_listener(ev, fn)
            except Exception:
                pass


def watch(page):
    """Start watching the page (called right after login, so the dashboard's first load is caught
    too). read_todo stops it."""
    if page not in _WATCHED:
        _WATCHED[page] = _Watch(page)
    return _WATCHED[page]


def _unwatch(page) -> tuple[list, list]:
    w = _WATCHED.pop(page, None)
    if not w:
        return [], []
    w.close(page)
    payloads, shapes = [], []
    for r in w.seen:
        try:
            j = r.json()
        except Exception:
            continue
        payloads.append(j)
        u = urlsplit(r.url)
        if "dashboard" in u.path and len(shapes) < 20:
            shapes.append({"path": u.path[:80], "method": r.request.method, "status": r.status,
                           "params": sorted({q.split("=")[0][:40] for q in u.query.split("&") if q}),
                           "response": _shape(j)})
    return payloads, shapes


def _eval(page, opts) -> dict:
    try:
        res = page.evaluate(SCRAPE_JS, opts)
        return res if isinstance(res, dict) else {"found": False}
    except Exception:          # the page is navigating, or the window was closed
        return {"found": False}


def _wait(page, ms):
    try:
        page.wait_for_timeout(ms)    # unlike time.sleep, lets the page's events through
    except Exception:
        time.sleep(ms / 1000)


def _wheel(page, res, dy):
    """A real mouse wheel over the To Do column (the page's own scrolling, exactly as a hand does it)."""
    try:
        x, y = int(res.get("wx")), int(res.get("wy"))
        if x > 0 and y > 0:
            page.mouse.move(x, y)
            page.mouse.wheel(0, dy)
    except Exception:
        pass


def _cards(res) -> list[dict]:
    out = []
    for c in (res.get("cards") or [])[:MAX_CARDS]:
        labels = parse_cards(c.get("label") if isinstance(c, dict) and isinstance(c.get("label"), str) else "")
        if len(labels) == 1:
            out.append({"label": labels[0], "term": clean_term(c.get("term")), "kind": clean_term(c.get("kind"))[:20]})
    return out


def _int(x):
    return x if isinstance(x, int) and not isinstance(x, bool) and 0 <= x < 100_000 else None


def read_todo(page, progress=print, stop=None, ready=None, on_seen=None, auto=None, reload=None,
              ask="click Read my To Do column", wait_s=1800, load_s=1800, quiet_s=45, stuck_s=300,
              recheck=False, step_ms=400, short_s=180) -> dict:
    """Read the To Do column of the dashboard open in `page`.

    Guided (ready is a threading.Event, the default from the web tab and the command line): the
    reviewer sets Approvals / Term in the window and sets `ready`. They don't have to wait for the
    column to load: its count goes up as cards arrive, so nobody can tell when it's done by looking.
    Auto (auto=True or no ready): clicks Approvals itself.

    Either way it then keeps scrolling and reading until the column has settled: at the bottom,
    no new cards and no change in the count for quiet_s, no spinner or open request (ignored once
    nothing has changed for stuck_s), and at least the count read. If the welcome line says more
    tasks than the column has, or fewer cards were read than the count, it waits 3x as long / up to short_s
    first. load_s caps the whole read. stop: a threading.Event that ends it early.
    recheck: the column was read before in this window; just read it again, as it is now.
    step_ms: pause between scroll steps (lazy lists need a moment to draw).
    short_s: while fewer cards than the column's count have been read, keep scrolling this long
    with nothing new before giving up (with a warning).
    on_seen(info) gets live numbers for the web page.

    Returns {"cards": [{"label", "term"}], "labels", "count", "capped", "tasks", "term", "toggle",
    "settled", "api", "api_docs", "from_api", "problem"?}. cards is empty when it couldn't be read."""
    auto = ready is None if auto is None else auto
    net = watch(page)
    state = {"count": None, "capped": False, "tasks": None, "term": "", "toggle": None, "settled": False}

    def report(phase, res, **extra):
        if res.get("found"):
            state["count"], state["capped"] = _int(res.get("count")), bool(res.get("capped"))
        state["tasks"] = _int(res.get("tasks"))
        state["term"] = clean_term(res.get("term"))
        state["toggle"] = res.get("toggle") if res.get("toggle") in ("All", "Syllabus", "Approvals") else None
        if on_seen:
            on_seen({"phase": phase, "found": bool(res.get("found")), "on_screen": len(res.get("cards") or []),
                     **state, **extra})

    def result(cards=(), problem=None, shapes=()):
        out = {"cards": list(cards), "labels": [c["label"] for c in cards], "api": list(shapes),
               "api_docs": {}, "from_api": 0, **state}
        if problem:
            out["problem"] = problem
        return out

    stopped = lambda: stop is not None and stop.is_set()
    cards, elsewhere = [], {}      # elsewhere: (label, term) seen in Not Ready / Awaiting Approval / Completed
    try:
        if reload or (reload is None and "/dashboard" not in (page.url or "")):
            from .syllabus_doc import BASE_URL
            page.goto(f"{BASE_URL}/en-US/dashboard", wait_until="domcontentloaded")

        # 1. Get to the column the reviewer wants.
        deadline = time.time() + wait_s
        if recheck:
            pass
        elif auto:
            progress("Waiting for the dashboard (this can take a few minutes)...")
        else:
            progress(f"In the Simple Syllabus window: pick Approvals and the terms you want, then {ask}. "
                     "No need to wait for it to finish loading.")
            ready.clear()        # a click or Enter from before the dashboard was up doesn't count
        while not recheck:
            if stopped():
                return result(problem="Stopped.")
            res = _eval(page, {})
            report("loading" if auto else "waiting", res)
            if auto and res.get("found"):
                if res.get("toggle") != "Approvals":
                    try:   # the reviewer's queue is under Approvals
                        page.get_by_text("Approvals", exact=True).first.click(timeout=3000)
                    except Exception:
                        pass
                    _wait(page, 1500)
                break
            if not auto and ready.is_set():
                if res.get("found"):
                    break
                ready.clear()
                progress("Couldn't find the To Do column in the Simple Syllabus window. Go back to the dashboard "
                         f"(View: Columns), then {ask} again.")
            if time.time() > deadline:
                return result(problem="Couldn't find the To Do column. Is the dashboard on View: Columns?" if auto
                              else "Timed out waiting. Start again when the dashboard is ready.")
            _wait(page, 1000)

        # 2. Keep scrolling and reading until the column has settled.
        if not recheck:
            progress("Reading the To Do column. It keeps going until the column stops growing...")
        _eval(page, {"scroll": "top"})
        _wait(page, 800)
        keys = set()
        start = last_change = time.time()
        sig, misses, bottom, last_pos, still = None, 0, False, None, 0
        while True:
            if stopped():
                return result(problem="Stopped.")
            res = _eval(page, {"scroll": "nudge" if bottom else "down"})
            if res.get("found"):
                # Some lists only move (or only load more) for a real mouse wheel, like his hand did.
                if bottom:
                    _wheel(page, res, -300)
                    _wheel(page, res, 900)
                elif not res.get("moved"):
                    _wheel(page, res, int((res.get("view") or 600) * 0.6))
            if not res.get("found"):
                misses += 1
                if misses > 30:
                    progress("Lost the To Do column (did the window leave the dashboard?). Using what was read.")
                    break
                _wait(page, 1000)
                continue
            misses = 0
            count, toggle = _int(res.get("count")), res.get("toggle")
            if sig and (toggle != sig[3] or (count is not None and sig[0] is not None and count < sig[0])):
                progress("The dashboard changed (filters?), so starting the read over.")
                cards, keys, elsewhere = [], set(), {}
                _eval(page, {"scroll": "top"})
            for c in _cards(res):
                k = (c["label"], _norm_term(c["term"]))
                if k not in keys and len(cards) < MAX_CARDS:
                    keys.add(k)
                    cards.append(c)
            for raw in (res.get("others") or [])[:900]:
                col = raw.get("column") if isinstance(raw, dict) and raw.get("column") in OTHER_COLUMNS else None
                for c in _cards({"cards": [raw]}) if col else []:
                    elsewhere.setdefault((c["label"], _norm_term(c["term"])), OTHER_COLUMNS[col])
            now = time.time()
            busy = bool(res.get("busy")) or net.busy()
            new_sig = (count, bool(res.get("capped")), len(cards), toggle)
            if new_sig != sig:
                sig, last_change = new_sig, now
            # At the bottom when the scroll box says so, or when nothing moves any more however it's scrolled.
            pos = res.get("pos")
            still = still + 1 if pos is not None and pos == last_pos else 0
            last_pos = pos
            quiet, bottom = now - last_change, bool(res.get("bottom")) or still >= 3
            if busy and quiet >= stuck_s:
                busy = False         # a spinner that never stops (or a stuck request): stop trusting it
            tasks = _int(res.get("tasks"))
            short = count is not None and not res.get("capped") and len(cards) < count
            # The count at the top is the target: fewer read means keep scrolling and loading much longer.
            need = max(short_s if short else 0, quiet_s * (3 if tasks and count is not None and count < tasks else 1))
            report("rechecking" if recheck else "reading", res, read=len(cards), busy=busy, quiet=int(quiet), need=int(need))
            if bottom and not busy and quiet >= need:
                state["settled"] = True
                break
            if len(cards) >= MAX_CARDS or now - start > load_s:
                progress(f"Still changing after {int((now - start) / 60)} minutes, so using what was read so far.")
                break
            _wait(page, max(step_ms, 1000) if bottom else step_ms)
        _eval(page, {"scroll": "top"})
    finally:
        payloads, shapes = _unwatch(page)

    # 3. Strictly To Do: a card that another column also shows is left out (and reported).
    key = lambda c: (c["label"], _norm_term(c["term"]))
    excluded = [{**c, "also_in": elsewhere[key(c)]} for c in cards if key(c) in elsewhere]
    cards = [c for c in cards if key(c) not in elsewhere]

    # 4. Anything the scroll didn't reach: fill in from a dashboard API list that is exactly this column.
    out = result(cards, shapes=shapes)
    out["excluded"] = excluded
    lists = api_lists(payloads)
    for ds in lists:
        for d in ds:
            same = out["api_docs"].setdefault(d["title"], [])
            if all(x["code"] != d["code"] for x in same):
                same.append(d)
    count = state["count"]
    if count and not state["capped"] and len(cards) < count:
        have = {c["label"] for c in cards}
        for ds in lists:
            labels = {d["title"] for d in ds}
            if len(ds) == count and len(labels) == count and have <= labels:
                extra = [{"label": d["title"], "term": d["term_name"], "kind": "api"} for d in ds if d["title"] not in have
                         and (d["title"], _norm_term(d["term_name"])) not in elsewhere]
                out["cards"] += extra
                out["labels"] += [c["label"] for c in extra]
                out["from_api"] = len(extra)
                break
    if not out["cards"] and count and not excluded:
        out["problem"] = (f"The To Do column says {count}{'+' if state['capped'] else ''} but no cards could be read "
                          "from it on its own, so nothing from other columns was used.")
    return out


def main() -> int:
    """python -m reviewer.dashboard: log in and show what's in the To Do column. Downloads nothing."""
    import threading
    from playwright.sync_api import sync_playwright
    from . import pull
    ready = threading.Event()
    with sync_playwright() as pw:
        session, request = pull.login(pw)
        try:
            threading.Thread(target=pull.enter_sets, args=(ready,), daemon=True).start()
            todo = read_todo(request.page, ready=ready, ask="press Enter here")
        finally:
            session.close()
    if not todo["cards"] and not todo.get("problem"):
        print(f"\nFound the To Do column on the {todo['term'] or 'selected'} dashboard. It's empty.")
        return 0
    if not todo["cards"]:
        print(f"\nCouldn't read the To Do column: {todo['problem']}")
        print("Dashboard API calls seen:", [a["path"] for a in todo["api"]] or "none")
        return 1
    print(f"\nTerm box: {todo['term'] or '(not found)'}   Toggle: {todo['toggle'] or '(not sure)'}")
    print(f"To Do column says {todo['count']}{'+' if todo['capped'] else ''}, read {len(todo['cards'])}"
          + (f" ({todo['from_api']} from the dashboard's data)" if todo["from_api"] else "") + ":")
    for c in todo["cards"]:
        print(f"  {c['label']}  {c['term']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
