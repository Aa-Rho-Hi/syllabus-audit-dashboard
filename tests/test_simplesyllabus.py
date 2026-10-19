import json
from pathlib import Path
from types import SimpleNamespace

from reviewer import pull
from reviewer.audit import audit_doc
from reviewer.syllabus_doc import course_label, file_stem, objectives, printable_html, slim

FIX = Path(__file__).parent / "fixtures" / "ss_doc_full_page.json"


def raw():
    return json.loads(FIX.read_text())["items"][0]["doc_data"]


def test_slim_drops_people_data():
    s = json.dumps(slim(raw()))
    assert "someone@tamu.edu" not in s and "123456789" not in s and "ca_7" not in s
    assert slim(raw())["term_name"] == "Fall 2025 - College Station"


def test_objectives_from_list_items():
    items = objectives(slim(raw()))
    assert items == ["understand theoretical and mathematical concepts about signals and systems",
                     "apply the concepts to analyze and design systems that process real-world signals."]


def test_objectives_from_paragraphs():
    doc = {"components": [{"html": '<h2 class="component-name">Learning Objectives</h2><div class="fr-view">'
                                   "<p>Students will be able to:</p><p>1. Explain sampling</p>"
                                   "<p>2. Design FIR filters for audio</p></div>"}]}
    assert objectives(doc) == ["1. Explain sampling", "2. Design FIR filters for audio"]


def test_labels_and_html():
    d = slim(raw())
    assert course_label(d) == "ECEN 314 502"
    assert file_stem(d) == "ECEN_314_502_41862_mjombsn1e"
    h = printable_html({**d, "status": "awaiting_approval"})
    assert '<base href="https://tamu.simplesyllabus.com/">' in h and "awaiting approval" in h


def test_audit_flags_understand(tmp_path):
    d = slim(raw())
    p = tmp_path / "Fall 2025" / "_data" / "x.json"
    p.parent.mkdir(parents=True)
    r = audit_doc(d, p, tmp_path, use_llm=False)
    assert r["course"] == "ECEN 314 502"
    assert [o["status"] for o in r["objectives"]] == ["avoid", "ok"]
    assert "understand" in r["objectives"][0]["avoid_terms"]
    assert r["file"]["link"].endswith("/doc/mjombsn1e")      # no PDF -> link back to Simple Syllabus


def test_draft_without_content(tmp_path):
    r = audit_doc({"code": "hqq0vr03t", "title": "ECEN 314 200 (38440)", "components": []},
                  tmp_path / "t" / "_data" / "x.json", tmp_path, use_llm=False)
    assert r["status"] == "unreadable" and "access" in r["summary"]


class FakeResp:
    def __init__(self, data):
        self.status, self._d = 200, data

    def json(self):
        return self._d


def test_list_docs_filters_and_pages():
    items = [{"code": f"c{i}", "entity_type": "section", "status": "awaiting_approval",
              "term_name": "Spring 2027 - College Station", "course_number": "314", "title": f"ECEN 314 {i}"}
             for i in range(3)]
    items += [{"code": "m", "entity_type": "course", "status": "awaiting_approval", "term_name": "Spring 2027"},
              {"code": "f", "entity_type": "section", "status": "completed", "term_name": "Spring 2027"},
              {"code": "o", "entity_type": "section", "status": "awaiting_approval", "term_name": "Fall 2026",
               "course_number": "314"}]
    seen = []

    def get(url, timeout=None):
        seen.append(url)
        return FakeResp({"sys": {"success": True}, "pagination": {"total": len(items)}, "items": items})

    args = SimpleNamespace(subject="ECEN", status="awaiting_approval", term="spring 2027", courses=["314"],
                           include_masters=False)
    out = pull.list_docs(SimpleNamespace(get=get), args)
    assert [d["code"] for d in out] == ["c0", "c1", "c2"]
    assert "statuses%5B%5D=awaiting_approval" in seen[0] and "subject_name=ECEN" in seen[0]


# Shape of /api2/heading-component for a draft awaiting approval (trimmed from a real ECEN 314 one).
def _v(name, value, status="passed", **kw):
    return {"variation_status": status, "combined": {"name": name, "value": value, "is_deleted": False,
                                                       "is_visible": True, **kw}}


HEADINGS = [
    {"component_type": "internal", "variations": [_v("Welcome to Simple Syllabus", "<p>Getting Started</p>")]},
    {"component_type": "content", "variations": [
        _v("Course Information ", '<p><span class="block-container"><span class="block-node block-prefix">Meeting Times: </span>'
           '<span class="block-node block-placeholder" data-block-name="section_ca_5">Section Meeting Times 1</span></span>'
           '<span class="block-container"><span class="block-node block-prefix">Meeting Location: </span>'
           '<span class="block-node block-placeholder" data-block-name="section_ca_14">Section Meeting Location 2</span></span></p>'),
        _v("Course Information", "<p>other campus</p>", status="failed")]},
    {"component_type": "instructor", "variations": [_v("Instructor Details ", "")],
     "columns": [{"heading": {"id": "c1"}, "variation": _v("Office Location", None)},
                 {"heading": {"id": "c2"}, "variation": _v("Photo", None, meta={"column_type": "image"})}],
     "rows": [{"variation": _v(None, None), "context": {"full_name": "Pat Example", "email": "pexample@tamu.edu", "ca_7": "SECRET"},
               "cells": [{"column_id": "c1", "variation": _v(None, "WEB 301FA")},
                         {"column_id": "c2", "variation": _v(None, "/ui/account-image/x")}]}]},
    {"component_type": "instructor", "variations": [_v("Instructor Details", "", is_deleted=True)], "rows": []},
    {"component_type": "content", "variations": [
        _v("Course Learning Outcomes", "<p>Upon completion of this course, the learner will be able to:</p><ul>"
           "<li>Describe signals mathematically and understand how to perform operations on signals.</li>"
           "<li>Identify commonly used signals such as the unit step and impulse.</li></ul>"),
        _v("Course Learning Objectives", "<p>MEPO table</p>", status="failed")]},
    {"component_type": "content", "variations": [_v("Additional Course Details", "<p></p>")]},
    {"component_type": "quickpick", "variations": [_v("Test", "")]},
]
PROPS = {"ca_5": "Meeting Type: LEC<br>Meeting Days: TR", "ca_14": "", "name": "200 (38440)"}


def test_components_from_headings():
    from reviewer.syllabus_doc import components_from_headings
    comps = components_from_headings(HEADINGS, PROPS)
    html = "".join(c["html"] for c in comps)
    assert len(comps) == 3                              # internal, deleted, empty, quickpick, failed all skipped
    assert "Meeting Days: TR" in html and "Section Meeting Times 1" not in html
    assert "Meeting Location" not in html               # empty block dropped
    assert "Pat Example" in html and "WEB 301FA" in html and "SECRET" not in html and "account-image" not in html
    assert "other campus" not in html and "MEPO" not in html
    doc = {"components": comps}
    assert objectives(doc)[1] == "Identify commonly used signals such as the unit step and impulse."


def test_fetch_doc_uses_draft_when_not_published():
    calls = []

    def get(url, timeout=None):
        calls.append(url)
        if "doc-full-page-get" in url:
            return FakeResp({"items": [{"doc_data": {"code": "hqq0vr03t", "title": "ECEN 314 200 (38440)",
                                                     "status": "awaiting_approval", "components": [],
                                                     "term": {"name": "Spring 2027 - College Station"},
                                                     "properties": PROPS}}]})
        return FakeResp({"items": HEADINGS})

    d = {"code": "hqq0vr03t", "entity_id": "E1", "term_id": "T1", "status": "awaiting_approval"}
    doc, status = pull.fetch_doc(SimpleNamespace(get=get), d)
    assert status == 200 and doc["source"] == "draft" and len(objectives(doc)) == 2
    assert "entity_id=E1" in calls[1] and "term_id=T1" in calls[1]


def test_messy_real_world_outcomes():
    # Word symbol bullets, split <span>/<em> paragraphs, inline "•" bullets: all seen in real ECEN drafts
    li = {"components": [{"html": '<h2 class="component-name">Course Learning Outcomes</h2><div class="fr-view"><ul>'
                                  "<li> Explain the basics of memory.</li><li>\xa0 \xa0Be able to design a cache.</li></ul></div>"}]}
    assert objectives(li) == ["Explain the basics of memory.", "Be able to design a cache."]
    para = {"components": [{"html": '<h2 class="component-name">Course Learning Outcomes</h2><div class="fr-view">'
                                    "<p><span>Upon completion of this course, the learner will be able to:</span></p>"
                                    "<p><span><em>Analyze and design</em> analog-to-digital converters</span></p>"
                                    "<p>After this course students will be able to:• Apply noise analysis• Characterize amplifier noise</p></div>"}]}
    assert objectives(para) == ["Analyze and design analog-to-digital converters", "Apply noise analysis",
                                "Characterize amplifier noise"]


def test_import_export_end_to_end(tmp_path):
    from reviewer import import_export
    from reviewer.audit import main as audit_main
    export = {"docs": [
        {"listing": {"code": "hqq0vr03t", "title": "ECEN 314 200 (38440)", "term_name": "Spring 2027 - College Station",
                     "status": "awaiting_approval", "course_number": "314", "subject_name": "ECEN"},
         "doc": {"code": "hqq0vr03t", "title": "ECEN 314 200 (38440)", "status": "awaiting_approval",
                 "term": {"name": "Spring 2027 - College Station"}, "properties": PROPS, "components": []},
         "headings": HEADINGS},
        {"listing": {"code": "../../bad", "title": None}, "doc": "garbage", "headings": "nope"},
        "not even a dict"]}
    src = tmp_path / "export.json"
    src.write_text(json.dumps(export))
    out = tmp_path / "run"
    import_export.main([str(src), "--out", str(out), "--no-pdf"])
    data = sorted(out.glob("*/_data/*.json"))
    assert len(data) == 2 and all(out in p.parents for p in data)
    assert audit_main([str(out), "--no-ai"]) == 0
    res = json.loads((out / "audit-results.json").read_text())["results"]
    assert any(r["course"] == "ECEN 314 200" and len(r["objectives"]) == 2 for r in res)
