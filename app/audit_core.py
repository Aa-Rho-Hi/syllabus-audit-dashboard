"""Checks a list of learning objectives. Shared by the web reviewer (Drive files) and the
Simple Syllabus command-line review (reviewer/audit.py)."""
from __future__ import annotations

from . import llm
from .verbs import final_status, normalize_objective, regex_check


def check_items(items: list[str], use_llm: bool, method: str = "regex", presplit: bool = False) -> dict:
    """Returns {status, objectives, method, summary} for one syllabus's objectives.
    presplit=True when items are already one objective each (e.g. Simple Syllabus list items),
    so a lowercase line isn't glued onto the one before it."""
    if not items:
        return {"status": "no_objectives", "objectives": [], "method": method,
                "summary": "No learning objectives section found."}

    norm = [normalize_objective(it) for it in items]
    tampered_texts = {t for t, bad in norm if bad}
    items = [t for t, _ in norm]
    if presplit:
        rx = [r for it in items for r in regex_check(" ".join(it.split()))]
        for i, r in enumerate(rx):
            r["index"] = i
    else:
        rx = regex_check("\n".join(items))
    ai = llm.check_objectives([r["text"] for r in rx]) if use_llm and any(r["status"] != "ok" for r in rx) else None
    objs = []
    for r in rx:
        a = next((x for x in (ai or []) if x.get("index") == r["index"]), {})
        status = final_status(r, a)
        suggestion = a.get("suggestion") if status != "ok" else None
        if not suggestion and r["flags"] and r["leading_verb"] and r["flags"][0]["start"] == r["text"].find(r["leading_verb"]):
            sug = r["flags"][0]["suggest"]
            if sug:
                suggestion = sug[0] + r["text"][r["text"].find(r["leading_verb"]) + len(r["leading_verb"]):]
        reasons = [i.get("reason") for i in (a.get("issues") or []) if i.get("reason")
                   and str(i.get("span", "")).lower() not in {fl["match"].lower() for fl in r["flags"]}]
        if any(r["text"] in t or t in r["text"] for t in tampered_texts):
            reasons.append("has hidden characters, look-alike letters or a word split with a hyphen; ask the professor to retype it")
            if status == "ok":
                status = "vague"
        objs.append({
            "text": r["text"], "status": status,
            "bloom_level": a.get("bloom_level") or (r["bloom_levels"] or [None])[0],
            "avoid_terms": [fl["base"] for fl in r["flags"]],
            "reasons": reasons,
            "suggestion": suggestion,
        })
    bad = [o for o in objs if o["status"] != "ok"]
    status = "clean" if not bad else ("avoid" if any(o["avoid_terms"] or o["status"] == "avoid" for o in bad) else "vague")
    return {"status": status, "objectives": objs, "method": method,
            "summary": f"{len(bad)} of {len(objs)} objectives need changes" if bad else f"All {len(objs)} objectives look good"}
