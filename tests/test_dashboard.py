"""--mine: reading the reviewer's To Do column off their Simple Syllabus dashboard.

End-to-end tests run reviewer/pull.py against fake_ss.Site, a copy of Aydin's dashboard from
photos (2026-10-20): All/Syllabus/Approvals, multi-select Term, To Do 42 loading slowly,
cards from several terms, and one To Do card the public list doesn't have.
"""
import functools
import json
import threading
import time
from types import SimpleNamespace

import pytest

import fake_ss
from reviewer import dashboard, pull

REAL_READ_TODO = dashboard.read_todo


# ---------- running the real download against the fake site ----------

@pytest.fixture
def fake_browser(monkeypatch, tmp_path):
    """pull.launch -> headless Chromium whose every request goes to the fake site."""
    holder = {}

    def launch(pw, headless, **kw):
        if "user_data_dir" in kw:
            ctx = pw.chromium.launch_persistent_context(headless=True, **kw)
            ctx.route("**/*", holder["site"])
            return ctx
        return pw.chromium.launch(headless=True)
    monkeypatch.setattr(pull, "launch", launch)
    monkeypatch.setattr(pull, "prepare_profile", lambda: tmp_path / "profile")
    monkeypatch.setattr(pull, "logged_in", lambda req: "Test Reviewer")   # ctx.request isn't routed
    monkeypatch.setattr(pull, "time", SimpleNamespace(time=time.time, sleep=lambda s: None))
    monkeypatch.setattr(dashboard, "read_todo",
                        functools.partial(REAL_READ_TODO, quiet_s=1.5, stuck_s=20, load_s=40, wait_s=20, step_ms=150, short_s=4))
    return holder


def args(**kw):
    return SimpleNamespace(**{"subject": "ECEN", "term": "", "status": "awaiting_approval", "courses": [], "mine": True,
                              "crns": [], "auto": False, "ask": "press Read", "include_masters": False,
                              "no_pdf": True, **kw})


def run(holder, site, out, reviewer=None, **kw):
    """reviewer(info, seen) -> True when the 'reviewer' would click Read. None = auto mode."""
    holder["site"] = site
    log, seen = [], []
    ready = threading.Event() if reviewer else None

    def on_seen(info):
        seen.append(info)
        if reviewer and reviewer(info, seen):
            ready.set()
    manifest = pull.run_pull(args(auto=reviewer is None, **kw), out, progress=log.append, ready=ready, on_seen=on_seen)
    return manifest, log, seen


def saved(out):
    docs = [json.loads(p.read_text()) for p in out.glob("*/_data/*.json")]
    return {(d["title"], d["term_name"]) for d in docs}, docs


def want(data):
    return {(d["title"], d["term_name"]) for d in data["todo"]}


def test_guided_reads_exactly_his_todo(fake_browser, tmp_path):
    """He switches to Approvals himself and clicks Read right away, while the count is still
    climbing. The tool still gets all 42, each in the right term, and nothing else."""
    data = fake_ss.aydin()
    site = fake_ss.Site(data, start="All", auto_pick_ms=800, batch=5, batch_ms=200)
    early = lambda info, seen: info["phase"] == "waiting" and info["toggle"] == "Approvals" and 0 < (info["count"] or 0) < 42
    manifest, log, seen = run(fake_browser, site, tmp_path / "out", early, no_pdf=False)

    got, docs = saved(tmp_path / "out")
    assert got == want(data)                                              # exactly his To Do, 42
    assert len(manifest) == 42 and all(m["pdf"] for m in manifest)
    assert ("ECEN 314 200 (38440)", "Fall 2026 - College Station") in got   # not the Spring 2027 completed one
    assert all(d["components"] for d in docs)                             # drafts came through, incl. ECEN 489
    assert not any("214" in t or "325" in t for t, _ in got)              # his own syllabi / someone else's queue
    assert any(i["phase"] == "waiting" and i["toggle"] == "All" for i in seen)  # it really started on All
    assert any(i["phase"] == "reading" and i["count"] < 42 for i in seen)  # and the count was still climbing
    assert not any("Heads up" in m for m in log)
    assert any(m == "Nothing new." for m in log)                         # looked again at the end, found nothing

    check = json.loads((tmp_path / "out" / "dashboard-check.json").read_text())
    assert check["count"] == 42 and check["matched"] == 42 and check["missing"] == []
    for f in (tmp_path / "out").rglob("*"):                               # IDs from the dashboard API never saved
        if f.suffix in (".json", ".txt"):
            assert "SECRET" not in f.read_text() and "123456789" not in f.read_text()

    from reviewer.audit import run_audit
    results, _, xlsx = run_audit(tmp_path / "out", use_llm=False, progress=lambda m: None)
    assert len(results) == 42 and xlsx.exists()
    assert all(any(o["status"] == "avoid" for o in r["objectives"]) for r in results)   # "Understand ..." flagged


def test_auto_picks_approvals_and_waits_for_slow_load(fake_browser, tmp_path):
    """--auto: starts on All (54), clicks Approvals itself, waits for the column to stop changing."""
    data = fake_ss.aydin()
    site = fake_ss.Site(data, start="All", batch=4, batch_ms=250, mode="page")
    run(fake_browser, site, tmp_path / "out", no_recheck=True)
    assert saved(tmp_path / "out")[0] == want(data)


def test_cards_that_never_draw_come_from_dashboard_data(fake_browser, tmp_path):
    """The page never draws the last 2 cards; the dashboard's own API list of exactly 42 fills them in."""
    data = fake_ss.aydin()
    site = fake_ss.Site(data, start="Approvals", hide_last=2, batch=21, batch_ms=100)
    loaded = lambda info, seen: info["phase"] == "waiting" and len(seen) > 3
    manifest, log, _ = run(fake_browser, site, tmp_path / "out", loaded, no_recheck=True)
    assert saved(tmp_path / "out")[0] == want(data)
    assert any("2 of them from the dashboard's own data" in m for m in log)


def guided_now(info, seen):
    return info["phase"] == "waiting" and info["found"]          # clicks Read the moment the column shows


@pytest.mark.parametrize("opts, quiet", [
    ({"lazy": True, "batch": 5, "batch_ms": 100}, 1.5),            # more cards only load at the bottom
    ({"pause": (20, 9000), "batch": 5, "batch_ms": 100}, 1.5),     # loading stalls 9 s, spinner still on
    ({"pause": (20, 9000), "spinner": False, "batch": 5, "batch_ms": 100}, 3.5),  # no spinner: "You have 42 tasks" holds it (3 x 3.5 s)
    ({"spinner": "always", "batch": 7, "batch_ms": 100}, 1.5),     # a spinner that never stops can't hang it
], ids=["infinite-scroll", "stall-with-spinner", "stall-no-spinner", "stuck-spinner"])
def test_count_that_keeps_climbing(fake_browser, tmp_path, monkeypatch, opts, quiet):
    """Stalls are longer than reading the cards takes, so stopping on "nothing new for a bit"
    alone would end at 20 (checked by hand: no spinner, no tasks line, 9 s stall -> 20)."""
    monkeypatch.setattr(dashboard, "read_todo", functools.partial(REAL_READ_TODO, quiet_s=quiet, stuck_s=12, load_s=90, step_ms=150, short_s=4))
    data = fake_ss.aydin()
    site = fake_ss.Site(data, start="Approvals", **opts)
    manifest, log, seen = run(fake_browser, site, tmp_path / "out", guided_now, no_recheck=True)
    assert saved(tmp_path / "out")[0] == want(data)
    assert not any("Heads up" in m for m in log)


@pytest.mark.parametrize("lazy", [True, "wheel"], ids=["loads-on-scroll", "loads-only-on-mouse-wheel"])
@pytest.mark.parametrize("count", ["total", "loaded", "low"])
def test_his_layout_outer_box_scrolls(fake_browser, tmp_path, lazy, count):
    """2026-10-21 on his machine: the page itself doesn't scroll, a box around the board does, and
    more cards only load when that's scrolled to the bottom. He had to scroll by hand. The header
    count may be the full number, climb as cards load, or even be a bit low: all 42 either way."""
    data = fake_ss.aydin()
    site = fake_ss.Site(data, start="Approvals", batch=6, batch_ms=100, mode="page", scroller="main", lazy=lazy, count=count)
    manifest, log, _ = run(fake_browser, site, tmp_path / "out", guided_now, no_recheck=True)
    assert saved(tmp_path / "out")[0] == want(data)


def test_filters_changed_mid_read_starts_over(fake_browser, tmp_path):
    data = fake_ss.aydin()
    site = fake_ss.Site(data, start="Approvals", batch=6, batch_ms=150, then_pick=("All", 2000))
    manifest, log, _ = run(fake_browser, site, tmp_path / "out", guided_now, no_recheck=True)
    assert any("starting the read over" in m for m in log)
    assert len(manifest) == 54 and any("was on All, not Approvals" in m for m in log)


def test_tasks_added_while_it_runs_get_picked_up(fake_browser, tmp_path):
    """Two syllabi land in his To Do while the tool is downloading. The look at the end gets them."""
    data = fake_ss.aydin()
    site = fake_ss.Site(data, start="Approvals", batch=14, batch_ms=100, live=True)
    manifest, log, seen = run(fake_browser, site, tmp_path / "out", guided_now)
    assert saved(tmp_path / "out")[0] == want(data) | {(d["title"], d["term_name"]) for d in data["late"]}
    assert len(manifest) == 44 and any(m.startswith("2 new in To Do: ECEN 449 501 (58001)") for m in log)
    assert any(i["phase"] == "rechecking" for i in seen)
    check = json.loads((tmp_path / "out" / "dashboard-check.json").read_text())
    assert check["added_later"] == ["ECEN 449 501 (58001)", "ECEN 449 502 (58002)"]


def test_no_recheck_flag(fake_browser, tmp_path):
    data = fake_ss.aydin()
    site = fake_ss.Site(data, start="Approvals", batch=42, batch_ms=50, live=True)
    manifest, log, _ = run(fake_browser, site, tmp_path / "out", guided_now, no_recheck=True)
    assert len(manifest) == 42 and not any("Checking your To Do column again" in m for m in log)


def test_card_also_in_completed_is_left_out(fake_browser, tmp_path):
    """Strictly his To Do: a card the Completed or Not Ready column also shows never gets audited."""
    data = fake_ss.aydin()
    data["completed"].append(dict(data["todo"][3]))                      # same section and term, now Completed too
    data["not_ready"].append(dict(data["todo"][4]))
    site = fake_ss.Site(data, start="Approvals", batch=42, batch_ms=50)
    manifest, log, _ = run(fake_browser, site, tmp_path / "out", guided_now)
    got = saved(tmp_path / "out")[0]
    assert len(got) == 40 and got == want(data) - {(d["title"], d["term_name"]) for d in data["todo"][3:5]}
    assert any(m.startswith("Left out 2 card(s) that also show in another column") and "Completed" in m
               and "Not Ready" in m for m in log)
    check = json.loads((tmp_path / "out" / "dashboard-check.json").read_text())
    assert {c["also_in"] for c in check["excluded"]} == {"Completed", "Not Ready"}


def test_other_columns_never_leak_in():
    """Cards from Not Ready and Completed never come out as To Do, whatever the layout."""
    data = fake_ss.aydin()
    r = read_page(fake_ss.dashboard_html(data, start="Approvals", batch=42, mode="page"))
    labels = {c["label"] for c in r["cards"]}
    assert not labels & {d["title"] for d in data["not_ready"]}
    assert ("ECEN 314 200 (38440)", "Spring 2027 - College Station") not in {(c["label"], c["term"]) for c in r["cards"]}
    # One grid: a header row, then the four card lists side by side with no box per column.
    grid = ('<div style="display:grid;grid-template-columns:repeat(4,1fr)"><h3>Not Ready 2</h3><h3>To Do 3</h3>'
            '<h3>Awaiting Approval 0</h3><h3>Completed 1</h3>'
            + "".join(f'<div><p>Syllabus Spring 2027 - College Station</p><p>ECEN {n} 500 ({n}0)</p></div>' for n in (201, 301, 0, 401))
            + "</div>")
    r = read_page(f"<h1>You have 3 tasks</h1>{grid}")
    assert r["cards"] == [] and "no cards could be read" in r["problem"]


def test_reading_on_all_warns(fake_browser, tmp_path):
    data = fake_ss.aydin()
    site = fake_ss.Site(data, start="All", batch=54, batch_ms=50)
    manifest, log, _ = run(fake_browser, site, tmp_path / "out", lambda info, seen: len(seen) > 2, no_recheck=True)
    assert len(manifest) == 54 and any("was on All, not Approvals" in m for m in log)


def test_short_read_is_called_out(fake_browser, tmp_path, monkeypatch):
    """Without the API list, a column that stops short says so instead of quietly passing."""
    data = fake_ss.aydin()
    site = fake_ss.Site(data, start="Approvals", hide_last=3, batch=42, batch_ms=50)
    monkeypatch.setattr(site, "api_json", lambda view: {"nothing": []})
    loaded = lambda info, seen: info["phase"] == "waiting" and len(seen) > 2
    manifest, log, _ = run(fake_browser, site, tmp_path / "out", loaded, no_recheck=True)
    assert len(manifest) == 39 and any("only 39 of 42" in m for m in log)


class NoColumns(fake_ss.Site):
    def __call__(self, route, request):
        if request.url.endswith("/en-US/dashboard"):
            return route.fulfill(status=200, content_type="text/html", body="<h1>Welcome back</h1><div>View: List</div>")
        return super().__call__(route, request)


def test_no_column_never_downloads_something_else(fake_browser, tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "read_todo", functools.partial(REAL_READ_TODO, wait_s=3, step_ms=150, short_s=4))
    with pytest.raises(SystemExit, match="Couldn't read your To Do column"):
        run(fake_browser, NoColumns(fake_ss.aydin()), tmp_path / "out")
    assert not list((tmp_path / "out").glob("*/_data/*.json"))


def test_guided_read_without_column_asks_again_and_stop_works(fake_browser, tmp_path):
    stop, ready, calls, log = threading.Event(), threading.Event(), [], []

    def on_seen(info):
        calls.append(info)
        if len(calls) == 2:
            ready.set()          # clicks Read on the wrong page
        if len(calls) == 6:
            stop.set()           # then gives up
    fake_browser["site"] = NoColumns(fake_ss.aydin())
    with pytest.raises(pull.Stopped):
        pull.run_pull(args(), tmp_path / "out", progress=log.append, stop=stop, ready=ready, on_seen=on_seen)
    assert any("Couldn't find the To Do column in the Simple Syllabus window" in m for m in log)


# ---------- reading the page: hostile content ----------

def read_page(html, width=1400, **kw):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": width, "height": 900})
        page.set_content(html)
        try:
            return REAL_READ_TODO(page, progress=lambda m: None, auto=True, reload=False,
                                  **{"quiet_s": 1.5, "load_s": 30, "step_ms": 150, "short_s": 3, **kw})
        finally:
            b.close()


def test_spoofed_headers_and_lookalike_titles():
    data = fake_ss.aydin()
    data["todo"][5]["name"] = "ECEN 999 999 (11111)"                     # a title that looks like a label
    data["todo"][6]["name"] = '<img src=x onerror="document.title=1">To Do 999'
    spoof = '<div class="card"><p>To Do 7</p><p>ECEN 404 912 (56199)</p></div>'   # a Not Ready card saying "To Do"
    r = read_page(fake_ss.dashboard_html(data, start="Approvals", batch=42, mode="page", extra_card_html=spoof))
    assert r["count"] == 42
    assert {(c["label"], c["term"]) for c in r["cards"]} == want(data)
    assert r["toggle"] == "Approvals" and r["term"] == "Spring 2027 - College ..."


def test_count_next_to_other_header_bits():
    html = fake_ss.dashboard_html(fake_ss.aydin(), start="Approvals", batch=42, mode="page")
    old = '<h3 id="head">To Do <span id="n"></span> <i id="spin"></i></h3>'
    assert old in html
    html = html.replace(old, '<h3 id="head"><span>To Do</span> <b id="n"></b> <i id="spin"></i> <button>‹</button></h3>')
    r = read_page(html)
    assert r["count"] == 42 and len(r["cards"]) == 42


def his_layout(data=None, inject="", **opts):
    """Aydin's (2026-10-21): an outer box scrolls, more cards load only on a real wheel at the bottom."""
    base = dict(start="Approvals", batch=6, batch_ms=100, mode="page", scroller="main", lazy="wheel")
    return fake_ss.dashboard_html(data or fake_ss.aydin(), **{**base, **opts}).replace("</body>", inject + "</body>")


def many(n, first_crn):
    data = fake_ss.aydin()
    data["todo"] = [fake_ss.doc("ECEN", 100 + i % 800, 500 + i % 400, first_crn + i, "Spring 2027 - College Station")
                    for i in range(n)]
    return data


def cards_set(r):
    return {(c["label"], c["term"]) for c in r["cards"]}


def test_scroll_with_banner_over_the_column():
    banner = '<div style="position:fixed;left:0;right:0;bottom:0;height:260px;background:#fee;z-index:9">Cookies</div>'
    assert cards_set(read_page(his_layout(inject=banner))) == want(fake_ss.aydin())


def test_scroll_with_popup_over_everything():
    modal = ('<script>setTimeout(() => { const m = document.createElement("div"); m.textContent = "Session expiring";'
             'm.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:99"; document.body.append(m); }, 800)</script>')
    assert cards_set(read_page(his_layout(inject=modal))) == want(fake_ss.aydin())


def test_scroll_when_cards_scroll_inside_themselves():
    h = his_layout().replace("height:110px}", "height:110px;overflow:auto}").replace(
        'Object.assign(document.createElement("div"), {textContent: "✓ 1/2"})',
        'Object.assign(document.createElement("div"), {textContent: "✓ 1/2 " + "long note ".repeat(80)})')
    assert cards_set(read_page(h)) == want(fake_ss.aydin())


def test_hidden_decoy_column_and_narrow_window():
    decoy = ('<div style="visibility:hidden;position:absolute;top:0"><h3>To Do 1</h3>'
             '<div class="card"><p>Syllabus Spring 2027 - College Station</p><p>ECEN 999 501 (99999)</p></div></div>')
    r = read_page(his_layout().replace('<div id="board"', decoy + '<div id="board"'), width=700)
    assert r["count"] == 42 and cards_set(r) == want(fake_ss.aydin())


def test_more_than_500_cards_on_screen():
    r = read_page(his_layout(many(600, 10000), batch=100, batch_ms=30, lazy=True), load_s=60)
    assert len(r["cards"]) == 600


def test_never_ending_list_stops_at_the_cap():
    t = time.time()
    r = read_page(his_layout(many(3000, 20000), batch=40, batch_ms=50, lazy=True), load_s=8)
    assert time.time() - t < 25 and not r["settled"] and r["cards"]


def test_page_text_only_yields_card_labels():
    evil = ["ECEN 314 200 (38440)<img src=x onerror=alert(1)>", "../../etc ECEN 999 999 (1)", "=HYPERLINK(\"x\")"]
    assert dashboard.parse_cards(" ".join(evil)) == ["ECEN 314 200 (38440)"]
    assert dashboard.clean_term('=HYPERLINK("x")<script>Spring 2027 - Q</script>/../') == \
        "HYPERLINK x script Spring 2027 - Q script .."
    cards = dashboard._cards({"cards": [{"label": "<b>"}, {"label": 5}, "junk"]
                                       + [{"label": f"ECEN 300 500 ({i})", "term": "x" * 500} for i in range(10000, 12500)]})
    assert len(cards) == dashboard.MAX_CARDS - 3 and all(len(c["term"]) <= 80 for c in cards)


def test_api_docs_only_keep_safe_fields():
    good = {"title": "ECEN 489 600 (59999)", "code": "c59999t27s", "entity_id": "e1", "term_id": "t27s",
            "term_name": "Spring 2027 - College Station", "status": "awaiting_approval", "account_id": "SECRET", "ca_7": "1"}
    d = dashboard.api_doc(good)
    assert d["code"] == "c59999t27s" and "account_id" not in d and "ca_7" not in d
    for bad in ({**good, "code": "../../etc/passwd"}, {**good, "code": ""}, {**good, "entity_type": "course"},
                {**good, "title": "ECEN 1 (x)"}, {**good, "title": "ECEN 314 200 (1) ECEN 315 200 (2)"}, "x", None):
        assert dashboard.api_doc(bad) is None
    d = dashboard.api_doc({**good, "entity_id": "1; DROP TABLE", "status": "<b>", "term_name": "../../x"})
    assert "entity_id" not in d and "status" not in d and "/" not in d["term_name"]
    deep = {"a": [{"b": [{"c": [good]}]}]}
    assert [x["code"] for ds in dashboard.api_lists([deep, "junk", [None, 1]]) for x in ds] == ["c59999t27s"]


# ---------- matching cards to docs ----------

def docs():
    mk = lambda t, term="Spring 2027 - College Station", **kw: {"title": t, "term_name": term, "entity_type": "section",
                                                                   "code": t[-6:-1] + term[:4], "course_number": t.split()[1], **kw}
    return [mk("ECEN 314 200 (38440)", status="completed"),
            mk("ECEN 303 503 (56552)", status="in_progress"),
            mk("ECEN 314 200 (38440)", term="Fall 2026 - College Station"),
            mk("ECEN 325 501 (10747)", status="awaiting_approval")]


def test_match_uses_each_cards_term():
    c = lambda label, term: {"label": label, "term": term}
    found, missing = dashboard.match_cards([c("ECEN 314 200 (38440)", "Fall 2026 - College Station"),
                                            c("ECEN 303 503 (56552)", ""),                 # only one: fine
                                            c("ECEN 325 501 (10747)", "Spring 2027 - College ..."),
                                            c("ECEN 404 912 (56199)", "Spring 2027 - College Station")], docs())
    assert [(d["title"], d["term_name"][:6]) for d in found] == [("ECEN 314 200 (38440)", "Fall 2"),
                                                                 ("ECEN 303 503 (56552)", "Spring"),
                                                                 ("ECEN 325 501 (10747)", "Spring")]
    assert missing == ["ECEN 404 912 (56199) (Spring 2027 - College Station)"]


def test_match_never_takes_completed_or_another_term():
    c = lambda label, term: {"label": label, "term": term}
    found, missing = dashboard.match_cards([c("ECEN 314 200 (38440)", "Spring 2027 - College Station"),  # completed
                                            c("ECEN 303 503 (56552)", "Fall 2026 - College Station"),    # only Spring exists
                                            c("ECEN 777 500 (10001)", "")], docs()
                                           + [{"title": "ECEN 777 500 (10001)", "term_name": "Fall 2026", "code": "n",
                                               "status": "not_started", "entity_type": "section"}])
    assert found == []
    assert missing == ["ECEN 314 200 (38440) (Spring 2027 - College Station): Simple Syllabus lists it as completed, "
                       "so it's left out", "ECEN 303 503 (56552) (Fall 2026 - College Station)",
                       "ECEN 777 500 (10001): Simple Syllabus lists it as not started, so it's left out"]


def test_match_never_guesses_between_terms():
    found, missing = dashboard.match_cards([{"label": "ECEN 314 200 (38440)", "term": ""}], docs())
    assert found == [] and missing == ["ECEN 314 200 (38440)"]


def test_match_falls_back_to_dashboard_data():
    api = {"ECEN 489 600 (59999)": [dashboard.api_doc({"title": "ECEN 489 600 (59999)", "code": "zz9"})]}
    found, missing = dashboard.match_cards([{"label": "ECEN 489 600 (59999)", "term": ""}], docs(), api)
    assert [d["code"] for d in found] == ["zz9"] and missing == []


def test_match_typed_crns():
    found, missing = dashboard.match_crns(["56552", "99999", "x;rm"], docs(), "Spring 2027")
    assert [d["title"] for d in found] == ["ECEN 303 503 (56552)"] and missing == ["99999"]


def test_old_label_matching_still_works():
    found, missing = dashboard.match_docs(["ECEN 325 501 (10747)", "ECEN 303 503 (56552)", "ECEN 404 912 (56199)"],
                                          docs(), "Spring 2027 - College ...")
    assert [(d["title"], d["term_name"][:6]) for d in found] == [("ECEN 325 501 (10747)", "Spring"),
                                                                 ("ECEN 303 503 (56552)", "Spring")]
    assert missing == ["ECEN 404 912 (56199) (Spring 2027 - College ...)"]


# ---------- my_docs and the command line ----------

def test_empty_todo_downloads_nothing(tmp_path, monkeypatch):
    msgs = []
    monkeypatch.setattr(dashboard, "read_todo", lambda page, progress, **kw: {"cards": [], "labels": [], "count": 0})
    monkeypatch.setattr(pull, "list_docs", lambda req, a: docs())
    assert pull.my_docs(SimpleNamespace(page=None), args(), tmp_path, progress=msgs.append) == []
    assert any("empty" in m for m in msgs)


def test_cli_flags():
    seen = {}
    real = pull.run_pull
    pull.run_pull = lambda a, out, **kw: seen.update(vars(a), **kw) or []
    try:
        pull.main(["--mine", "--crns", "56552", "38440", "--out", "/tmp/x-not-used"])
        assert seen["mine"] is True and seen["crns"] == ["56552", "38440"] and seen["ready"] is None
        pull.main(["--mine", "--auto", "--out", "/tmp/x-not-used"])
        assert seen["auto"] is True and seen["ready"] is None
        pull.main(["--mine", "--everyone", "--out", "/tmp/x-not-used"])
        assert seen["mine"] is False
    finally:
        pull.run_pull = real
