from app.overlap import find_overlaps
from app.report import faculty_note
from app.llm import suggestion_ok


def R(course, *objs, status="ok", sug=None):
    return {"course": course, "file": {"name": course.replace(" ", "") + ".docx"},
            "objectives": [{"text": o, "status": status, "avoid_terms": [], "reasons": [], "suggestion": sug} for o in objs]}


def test_overlaps_found_across_courses_only():
    res = [R("ECEN 214", "Analyze the fundamental electrical quantities of charge, current, voltage, power, and energy.",
             "Analyze circuits using circuit simulation software."),
           R("ECEN 215", "Analyze the fundamental electrical quantities of charge, current, voltage, power and energy",
             "Operate a simple microcontroller."),
           R("ECEN 350", "Design a pipelined datapath.")]
    pairs = find_overlaps(res)
    assert len(pairs) == 1 and pairs[0]["kind"] == "identical"
    assert res[0]["objectives"][0]["overlaps"] == ["ECEN 215 #1 (identical)"]
    assert res[1]["objectives"][0]["overlaps"] == ["ECEN 214 #1 (identical)"]
    assert res[2]["objectives"][0]["overlaps"] == []


def test_short_objectives_ignored():
    res = [R("ECEN 101", "Design a filter."), R("ECEN 102", "Design a filter.")]
    assert find_overlaps(res) == []


def test_faculty_note():
    r = R("ECEN 314", "Recognize the meaning of the frequency domain.", status="avoid",
          sug="Interpret signals in the frequency domain.")
    r["objectives"][0]["avoid_terms"] = ["recognize"]
    r["objectives"][0]["overlaps"] = []
    note = faculty_note(r)
    assert "ECEN 314" in note and '"recognize" isn\'t on the approved list' in note
    assert 'Suggested: "Interpret signals in the frequency domain."' in note
    assert "—" not in note and "–" not in note          # no em/en dashes


def test_note_drops_off_list_rewrites():
    r = R("ECEN 489", "Learn wireless security.", status="vague", sug="Configure a secure network.")
    r["objectives"][0]["overlaps"] = []
    note = faculty_note(r)
    assert "Configure" not in note and "approved verb such as Explain" in note


def test_note_for_missing_and_clean():
    assert "couldn't find learning objectives" in faculty_note({"course": "ECEN 404", "file": {"name": "x.docx"}, "objectives": []})
    clean = R("ECEN 350", "Design a pipelined datapath.")
    clean["objectives"][0]["overlaps"] = []
    assert faculty_note(clean) == ""


def test_suggestion_ok():
    assert suggestion_ok("Explain the TLS handshake.") and not suggestion_ok("Configure X.")
