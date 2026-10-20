"""--mine: reading the reviewer's To Do column off their Simple Syllabus dashboard."""
import json
from types import SimpleNamespace

from reviewer import dashboard, pull

TODO = [f"ECEN {300 + i} {500 + i} ({56500 + i})" for i in range(31)]


def fake_dashboard(todo=TODO, lazy=True, header="To Do"):
    """Laid out like Aydin's dashboard on 2026-10-16: four columns, counts in badges, 99+ caps.
    With lazy=True the To Do list only puts the cards near the scroll position in the page."""
    card = lambda t, name="SIGNALS AND SYSTEMS": (f'<div class="card"><span class="tag">Syllabus</span>'
                                                  f'<span>Spring 2027 - College Station</span><p>{t}</p><h4>{name}</h4>'
                                                  f'<button>Review</button><div>✓ 1/2</div></div>')
    other = "".join(card(f"ECEN 214 {200 + i} ({10700 + i})", "ELEC CIRCUIT THEORY") for i in range(5))
    return f"""<html><body>
    <h1>Welcome back, Aydin. <small>You have {len(todo)} tasks to complete</small></h1>
    <div><button>All</button><button>Syllabus</button><button class="on">Approvals</button></div>
    <fieldset><label>Term</label><div>Spring 2027 - College Station</div><input value="5521" hidden></fieldset>
    <fieldset><label>View</label><div>Columns</div></fieldset>
    <div style="display:flex;gap:10px">
      <section><h3>Not Ready <span>99</span>+</h3>{other}</section>
      <section><h3>{header} <span>{len(todo)}</span></h3>
        <div id="list" style="height:400px;overflow-y:auto"><div id="inner"></div></div></section>
      <section><h3>Awaiting Approval <span>0</span></h3></section>
      <section><h3>Completed <span>99</span>+</h3>{other.replace("(107", "(119")}</section>
    </div>
    <script>
      const todo = {json.dumps(todo)}, H = 120, list = document.getElementById("list"), inner = document.getElementById("inner");
      const card = t => `{card("${t}")}`;
      function draw() {{
        const first = {"Math.floor(list.scrollTop / H)" if lazy else "0"}, n = {"5" if lazy else "todo.length"};
        inner.style.height = (todo.length * H) + "px"; inner.style.paddingTop = (first * H) + "px";
        inner.style.boxSizing = "border-box";
        inner.innerHTML = todo.slice(first, first + n).map(card).join("");
      }}
      list.addEventListener("scroll", draw); draw();
    </script></body></html>"""


def read(html, **kw):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        page.set_content(html)
        try:
            return dashboard.read_todo(page, progress=lambda m: None, reload=False, **kw)
        finally:
            b.close()


def test_reads_whole_todo_column_even_when_lazy():
    r = read(fake_dashboard())
    assert r["labels"] == TODO                      # all 31, in order, scrolled into view one by one
    assert r["count"] == 31 and not r["capped"]
    assert r["term"].startswith("Spring 2027 - College Station")
    assert not any("ECEN 214" in x for x in r["labels"])   # nothing from Not Ready / Completed


def test_no_todo_column_says_so():
    r = read(fake_dashboard(header="Things"), timeout_s=2)
    assert r["labels"] == [] and "View: Columns" in r["problem"]


def test_page_text_only_yields_card_labels():
    evil = ["ECEN 314 200 (38440)<img src=x onerror=alert(1)>", "../../etc ECEN 999 999 (1)", "=HYPERLINK(\"x\")"]
    assert dashboard.parse_cards(" ".join(evil)) == ["ECEN 314 200 (38440)"]


def docs():
    mk = lambda t, term="Spring 2027 - College Station", **kw: {"title": t, "term_name": term, "entity_type": "section",
                                                                   "code": t[-6:-1], "course_number": t.split()[1], **kw}
    return [mk("ECEN 314 200 (38440)", status="awaiting_approval"),
            mk("ECEN 303 503 (56552)", status="in_progress"),       # in his queue, not "awaiting approval" publicly
            mk("ECEN 314 200 (38440)", term="Fall 2026 - College Station"),
            mk("ECEN 325 501 (10747)", status="awaiting_approval")]  # someone else's queue


def test_match_by_crn_and_term():
    found, missing = dashboard.match_docs(["ECEN 314 200 (38440)", "ECEN 303 503 (56552)", "ECEN 404 912 (56199)"],
                                          docs(), "Spring 2027 - College ...")
    assert [(d["title"], d["term_name"][:6]) for d in found] == [("ECEN 314 200 (38440)", "Spring"),
                                                                 ("ECEN 303 503 (56552)", "Spring")]
    assert missing == ["ECEN 404 912 (56199)"]


def test_match_typed_crns():
    found, missing = dashboard.match_crns(["56552", "99999", "x;rm"], docs(), "Spring 2027")
    assert [d["title"] for d in found] == ["ECEN 303 503 (56552)"] and missing == ["99999"]


def args(**kw):
    return SimpleNamespace(**{"subject": "ECEN", "term": "", "status": "awaiting_approval", "courses": [],
                              "include_masters": False, "mine": True, "crns": [], **kw})


def test_my_docs_uses_dashboard_and_all_statuses(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(dashboard, "read_todo", lambda page, progress, **kw: {
        "labels": ["ECEN 303 503 (56552)", "ECEN 314 200 (38440)"], "count": 2, "term": "Spring 2027 - College Station"})
    monkeypatch.setattr(pull, "list_docs", lambda req, a: asked.append(a.status) or docs())
    got = pull.my_docs(SimpleNamespace(page=None), args(), tmp_path, progress=lambda m: None)
    assert asked == ["all"]
    assert [d["title"] for d in got] == ["ECEN 303 503 (56552)", "ECEN 314 200 (38440)"]
    check = json.loads((tmp_path / "dashboard-check.json").read_text())
    assert check["matched"] == 2 and check["missing"] == []


def test_my_docs_falls_back_when_column_unreadable(tmp_path, monkeypatch):
    msgs = []
    monkeypatch.setattr(dashboard, "read_todo", lambda page, progress, **kw: {"labels": [], "problem": "nope"})
    monkeypatch.setattr(pull, "list_docs", lambda req, a: [d for d in docs() if d.get("status") == a.status])
    got = pull.my_docs(SimpleNamespace(page=None), args(), tmp_path, progress=msgs.append)
    assert len(got) == 2 and any("Falling back" in m for m in msgs)


def test_empty_todo_downloads_nothing(tmp_path, monkeypatch):
    msgs = []
    monkeypatch.setattr(dashboard, "read_todo", lambda page, progress, **kw: {"labels": [], "count": 0})
    monkeypatch.setattr(pull, "list_docs", lambda req, a: docs())
    assert pull.my_docs(SimpleNamespace(page=None), args(), tmp_path, progress=msgs.append) == []
    assert any("empty" in m for m in msgs)


def test_cli_flags():
    seen = {}
    real = pull.run_pull
    pull.run_pull = lambda a, out: seen.update(vars(a)) or []
    try:
        pull.main(["--mine", "--crns", "56552", "38440", "--out", "/tmp/x-not-used"])
        assert seen["mine"] is True and seen["crns"] == ["56552", "38440"]
        pull.main(["--mine", "--everyone", "--out", "/tmp/x-not-used"])
        assert seen["mine"] is False
    finally:
        pull.run_pull = real
