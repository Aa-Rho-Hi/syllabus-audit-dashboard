"""A stand-in for tamu.simplesyllabus.com, built from photos of Aydin's dashboard (2026-10-20).

Served through Playwright routing at the real address, so reviewer/pull.py runs end to end
against it: login check, dashboard, doc-public, doc-full-page-get, heading-component.
"""
import json
from urllib.parse import parse_qs, urlsplit

SITE = "https://tamu.simplesyllabus.com"
TERM_IDS = {"Spring 2027 - College Station": "t27s", "Spring 2027 - Galveston": "t27g", "Spring 2027 - Qatar": "t27q",
            "Fall 2026 - College Station": "t26f", "Testing & Training SP27": "ttr"}


def doc(subj, num, sec, crn, term, status="awaiting_approval", name="SIGNALS AND SYSTEMS"):
    t = TERM_IDS[term]
    return {"title": f"{subj} {num} {sec} ({crn})", "term_name": term, "term_id": t, "code": f"c{crn}{t}",
            "entity_id": f"e{crn}{t}", "entity_type": "section", "status": status, "subject_name": subj,
            "course_number": str(num), "name": name}


def aydin():
    """His Approvals To Do (42): mostly Spring 2027, some Fall 2026 and Galveston, as in the photos."""
    todo = [doc("ECEN", 322, 200, 34317, "Spring 2027 - College Station", name="ELEC & MAGNETIC FIELDS"),
            doc("ECEN", 322, 501, 34319, "Spring 2027 - College Station", name="ELEC & MAGNETIC FIELDS")]
    todo += [doc("ECEN", 330 + i, 500 + i, 56000 + i, "Spring 2027 - College Station") for i in range(28)]
    todo += [doc("ECEN", 410 + i, 500, 47000 + i, "Fall 2026 - College Station", status="in_progress") for i in range(8)]
    todo += [doc("ECEN", 314, 200, 38440, "Fall 2026 - College Station")]        # same label as a Spring 2027 one
    todo += [doc("ECEN", 350, 501, 51111, "Spring 2027 - Galveston"),
             doc("ECEN", 360, 700, 52222, "Testing & Training SP27")]
    hidden = doc("ECEN", 489, 600, 59999, "Spring 2027 - College Station")    # not in doc-public at all
    todo.append(hidden)
    assert len(todo) == 42
    own = [doc("ECEN", 214, 500 + i, 59500 + i, "Spring 2027 - College Station", "in_progress", "ELEC CIRCUIT THEORY")
           for i in range(12)]                                                  # his own syllabi: All = 54
    not_ready = [doc("ECEN", 214, 200, 10722, "Spring 2027 - College Station", "not_started", "ELEC CIRCUIT THEORY"),
                 doc("ECEN", 214, 500, 59539, "Spring 2027 - College Station", "not_started", "ELEC CIRCUIT THEORY")]
    completed = [doc("ECEN", 303, 503, 56552, "Spring 2027 - College Station", "completed", "RANDOM SIGNALS & SYSTEMS"),
                 doc("ECEN", 314, 200, 38440, "Spring 2027 - College Station", "completed")]
    others = [doc("ECEN", 325, 501 + i, 10747 + i, "Spring 2027 - College Station") for i in range(10)]  # someone else's
    late = [doc("ECEN", 449, 501, 58001, "Spring 2027 - College Station"),        # submitted while the tool runs
            doc("ECEN", 449, 502, 58002, "Fall 2026 - College Station")]
    public = [d for d in todo if d is not hidden] + own + not_ready + completed + others + late
    return {"todo": todo, "own": own, "not_ready": not_ready, "completed": completed, "public": public, "late": late}


def dashboard_html(data, start="All", batch=6, batch_ms=150, mode="virtual", hide_last=0, auto_pick_ms=None,
                   extra_card_html="", count="loaded", spinner=True, lazy=False, pause=None, then_pick=None,
                   live=False, scroller=None):
    """mode: "virtual" (To Do is its own scroll box that only draws what's in view) or "page" (the
    whole page scrolls and cards pile up). Cards arrive `batch` at a time, like the real slow load.
    count: "loaded" (the header counts cards as they arrive, like Aydin's) or "total" (shows the
    final number right away). spinner: a spinning ring next to the count while loading.
    lazy: more cards only load while the list is scrolled to the bottom (infinite scroll).
    pause: (after_n, ms), loading stops for ms after n cards. hide_last: that many cards never get
    drawn (only the API has them). auto_pick_ms: the "reviewer" clicks Approvals after that long.
    then_pick: (view, ms), the reviewer switches again later (changes the filters mid-read).
    live: the page polls every 0.5 s and adds data["late"] to To Do once the site says so.
    scroller="main": like Aydin's (2026-10-21), the page itself doesn't scroll; an app box around
    the board does, and the column has no scroll box of its own. lazy="wheel": more cards only
    load on real mouse-wheel scrolling near the bottom (not on scripted scrolling)."""
    card_data = lambda ds: [{"label": d["title"], "term": d["term_name"], "name": d["name"],
                             "review": d.get("review", f"/en-US/review/{d['code']}")} for d in ds]
    cfg = {"approvals": card_data(data["todo"]), "own": card_data(data["own"]), "start": start, "batch": batch,
           "batch_ms": batch_ms, "mode": mode, "hide": hide_last, "auto": auto_pick_ms, "count": count,
           "spinner": spinner, "lazy": lazy, "pause": pause, "then": then_pick, "live": live, "scroller": scroller,
           "late": card_data(data.get("late") or [])}
    static = lambda ds: "".join(f'<div class="card"><div class="tag"><span>Syllabus</span> <span>{d["term_name"]}</span></div>'
                                f'<p>{d["title"]}</p><h4>{d["name"]}</h4><div>✓ 0/2</div><div>E G +8</div></div>' for d in ds)
    js_cfg = json.dumps(cfg).replace("</", "<\\/")
    return f"""<!doctype html><html><head><style>
      body{{font-family:sans-serif;margin:0}} .on{{background:#222;color:#fff}} .card{{border:1px solid #ccc;margin:6px;padding:6px;height:110px}}
      .col{{width:300px}} .menu{{display:none}}
      @keyframes spin{{to{{transform:rotate(360deg)}}}}
      #spin{{display:inline-block;width:12px;height:12px;border:2px solid #999;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite}}
    </style></head><body{' style="overflow:hidden;height:100vh"' if scroller == "main" else ""}>
    <div id="main"{' style="height:100vh;overflow:auto"' if scroller == "main" else ""}>
    <h1>Welcome back, Aydin. <small id="tasks"></small></h1>
    <div id="toggle"><button data-v="All">All</button><button data-v="Syllabus">Syllabus</button><button data-v="Approvals">Approvals</button></div>
    <div class="field"><span>Looking for something...</span><button>Filters</button></div>
    <div class="field"><label>Term</label><div class="val">Spring 2027 - College ...</div>
      <ul class="menu"><li><input type="checkbox" checked>Future</li><li><input type="checkbox" checked>Spring 2027 - College Station</li>
      <li><input type="checkbox" checked>Current</li><li><input type="checkbox" checked>Fall 2026 - College Station</li></ul></div>
    <div class="field"><label>View</label><div>Columns</div></div>
    <div id="board" style="display:flex;align-items:flex-start">
      <section class="col"><h3>Not Ready <span>99</span>+</h3>{static(data["not_ready"])}{extra_card_html}</section>
      <section class="col"><h3 id="head">To Do <span id="n"></span> <i id="spin"></i></h3><div id="list"><div id="inner"></div></div></section>
      <section style="writing-mode:vertical-rl;width:40px"><h3>Awaiting Approval <span>0</span></h3></section>
      <section class="col"><h3>Completed <span>99</span>+</h3>{static(data["completed"])}</section>
    </div>
    </div>
    <script>
      const C = {js_cfg}, H = 124, list = document.getElementById("list"), inner = document.getElementById("inner");
      let view = C.start, loaded = 0, timer = null, paused = false;
      const main = document.getElementById("main");
      const sc = () => C.scroller === "main" ? main : C.mode === "virtual" ? list : document.scrollingElement;
      const nearBottom = () => sc().scrollTop + sc().clientHeight >= sc().scrollHeight - 60;
      let wheeled = false;
      sc().addEventListener("wheel", () => {{ wheeled = true; }}, {{passive: true}});   // only wheels that reach the list
      if (C.mode === "virtual" && C.scroller !== "main") list.style.cssText = "height:520px;overflow-y:auto";
      const items = () => view === "Approvals" ? C.approvals : view === "All" ? C.approvals.concat(C.own) : C.own;
      function card(c) {{
        const d = document.createElement("div"); d.className = "card";
        const tag = document.createElement("div"); tag.className = "tag";
        tag.append(Object.assign(document.createElement("span"), {{textContent: "Syllabus"}}), " ",
                   Object.assign(document.createElement("span"), {{textContent: c.term}}));
        d.append(tag, Object.assign(document.createElement("p"), {{textContent: c.label}}),
                 Object.assign(document.createElement("h4"), {{textContent: c.name}}),
                 Object.assign(document.createElement("a"), {{textContent: "Review", href: c.review}}),
                 Object.assign(document.createElement("div"), {{textContent: "✓ 1/2"}}));
        return d;
      }}
      function draw() {{
        const all = items(), drawn = all.slice(0, Math.max(0, loaded - C.hide));
        document.getElementById("n").textContent = C.count === "loaded" ? loaded : C.count === "low" ? all.length - 2 : all.length;
        document.getElementById("spin").style.display = C.spinner === "always" || (C.spinner && loaded < all.length) ? "" : "none";
        document.getElementById("tasks").textContent = `You have ${{all.length}} tasks to complete`;
        inner.textContent = "";
        if (C.mode === "virtual" && C.scroller !== "main") {{
          const first = Math.floor(list.scrollTop / H);
          inner.style.cssText = `box-sizing:border-box;height:${{drawn.length * H}}px;padding-top:${{first * H}}px`;
          drawn.slice(first, first + 6).forEach(c => inner.append(card(c)));
        }} else drawn.forEach(c => inner.append(card(c)));
      }}
      function tick() {{
        const n = items().length;
        if (loaded >= n) return draw();
        let wait = C.batch_ms;
        if (C.pause && !paused && loaded >= C.pause[0]) {{ paused = true; wait = C.pause[1]; }}
        else if (!C.lazy || loaded === 0 || (nearBottom() && (C.lazy !== "wheel" || wheeled))) {{
          loaded = Math.min(n, loaded + C.batch); wheeled = false;
        }}
        draw(); timer = setTimeout(tick, wait);
      }}
      function pick(v) {{
        view = v; loaded = 0; paused = false; clearTimeout(timer);
        document.querySelectorAll("#toggle button").forEach(b => {{ const on = b.dataset.v === v;
          b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); }});
        fetch("/api2/dashboard-doc-search?view=" + v).then(r => r.json()).catch(() => null);
        draw(); timer = setTimeout(tick, C.batch_ms);
      }}
      document.querySelectorAll("#toggle button").forEach(b => b.onclick = () => pick(b.dataset.v));
      list.addEventListener("scroll", draw);
      pick(C.start);
      if (C.auto != null) setTimeout(() => pick("Approvals"), C.auto);
      if (C.then) setTimeout(() => pick(C.then[0]), C.then[1]);
      if (C.live) {{
        const poll = setInterval(() => fetch("/api2/dashboard-poll").then(r => r.json()).then(j => {{
          if (!j.late) return;
          clearInterval(poll); C.approvals = C.approvals.concat(C.late); loaded += C.late.length; draw();
        }}).catch(() => null), 500);
      }}
    </script></body></html>"""


def heading_items(entity_id):
    v = lambda name, value: {"variation_status": "passed", "combined": {"name": name, "value": value, "is_deleted": False,
                                                                          "is_visible": True, "is_name_visible": True}}
    return [{"component_type": "content", "variations": [v("Course Description", "<p>Signals for " + entity_id + "</p>")]},
            {"component_type": "content", "variations": [v("Course Learning Outcomes",
                                                             "<ul><li>Understand convolution</li><li>Analyze LTI systems</li></ul>")]}]


class Site:
    """Routes for a Playwright context. Records what the tool asked for."""

    def __init__(self, data, **page_opts):
        self.data, self.page_opts, self.asked = data, page_opts, []
        self.late_after = 10      # live pages: new tasks show up after the tool has fetched this many drafts

    def api_json(self, view):
        cols = {"todo": self.data["todo"] if view == "Approvals" else self.data["todo"] + self.data["own"],
                "not_ready": self.data["not_ready"], "completed": self.data["completed"]}
        return {"columns": {k: {"items": [{**d, "account_id": "SECRET", "ca_7": "123456789"} for d in v]}
                            for k, v in cols.items()}}

    def __call__(self, route, request):
        u = urlsplit(request.url)
        q = parse_qs(u.query)
        self.asked.append(u.path)
        js = lambda body, status=200: route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
        if u.netloc != "tamu.simplesyllabus.com":
            return route.abort()
        if u.path == "/en-US/dashboard":
            return route.fulfill(status=200, content_type="text/html", body=dashboard_html(self.data, **self.page_opts))
        if u.path == "/api/session":
            return js({"data": {"account": {"entity_id": "a1", "first_name": "Test", "last_name": "Reviewer"}}})
        if u.path == "/api2/dashboard-poll":
            return js({"late": self.asked.count("/api2/heading-component") >= self.late_after})
        if u.path == "/api2/dashboard-doc-search":
            return js(self.api_json(q.get("view", ["All"])[0]))
        if u.path == "/api2/doc-public":
            subj, statuses = q.get("subject_name", [""])[0], q.get("statuses[]", [])
            items = [d for d in self.data["public"] if d["subject_name"] == subj and (not statuses or d["status"] in statuses)]
            return js({"sys": {"success": True}, "items": items, "pagination": {"total": len(items)}})
        if u.path == "/api2/doc-full-page-get":
            return js({"items": []})
        if u.path == "/api2/heading-component":
            return js({"items": heading_items(q.get("entity_id", ["?"])[0])})
        return route.fulfill(status=404, body="")
