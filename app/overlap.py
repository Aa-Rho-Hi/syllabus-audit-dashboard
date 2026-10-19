"""Finds learning objectives that are the same or nearly the same across different courses."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .verbs import clean_objective

IDENTICAL = 95
NEARLY = 85


def _norm(text: str) -> str:
    t = clean_objective(text).lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def find_overlaps(results: list[dict], group=None) -> list[dict]:
    """Returns pairs: {a_course, a_idx, a_text, b_course, b_idx, b_text, score, kind}.
    Also annotates each objective dict in `results` with o["overlaps"] = ["ECEN 215 #1 (identical)", ...].
    group(result) -> key: objectives in results with the same key never count as overlaps
    (e.g. 19 sections of ECEN 325 sharing one syllabus). Defaults to the course label."""
    group = group or (lambda r: r["course"])
    items = []
    for r in results:
        for i, o in enumerate(r.get("objectives", []), 1):
            o["overlaps"] = []
            n = _norm(o["text"])
            if len(n.split()) >= 4:            # very short objectives match too easily
                items.append((r["course"], i, o, n, group(r)))

    pairs = []
    for x in range(len(items)):
        ca, ia, oa, na, ga = items[x]
        for y in range(x + 1, len(items)):
            cb, ib, ob, nb, gb = items[y]
            if ga == gb:
                continue
            score = max(fuzz.ratio(na, nb), fuzz.token_sort_ratio(na, nb))
            if score < NEARLY:
                continue
            kind = "identical" if score >= IDENTICAL else "nearly identical"
            pairs.append({"a_course": ca, "a_idx": ia, "a_text": oa["text"], "b_course": cb, "b_idx": ib,
                          "b_text": ob["text"], "score": int(round(score)), "kind": kind})
            oa["overlaps"].append(f"{cb} #{ib} ({kind})")
            ob["overlaps"].append(f"{ca} #{ia} ({kind})")
    pairs.sort(key=lambda p: (-p["score"], p["a_course"], p["b_course"]))
    return pairs
