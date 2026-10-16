"""Course number / name matching and learning-objective extraction (regex layer)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz


@dataclass
class CourseNumber:
    dept: str | None   # "CSCE"
    num: str | None    # "489"
    raw: str

    @property
    def pretty(self) -> str:
        return f"{self.dept} {self.num}" if self.dept else (self.num or self.raw)


def parse_course_number(raw: str) -> CourseNumber:
    raw = (raw or "").strip()
    m = re.search(r"([A-Za-z]{2,5})\s*[-_ ]?\s*(\d{3,4}[A-Za-z]?)", raw)
    if m:
        return CourseNumber(m.group(1).upper(), m.group(2).upper(), raw)
    m = re.search(r"(\d{3,4}[A-Za-z]?)", raw)
    return CourseNumber(None, m.group(1).upper() if m else None, raw)


def number_regex(cn: CourseNumber) -> re.Pattern | None:
    """Matches 'CSCE 489', 'CSCE-489', 'csce489' and cross-listed 'CSCE 489/689'."""
    if not cn.num:
        return None
    num = re.escape(cn.num)
    if cn.dept:
        dept = re.escape(cn.dept)
        return re.compile(
            rf"\b{dept}[\s\-_]*(?:\d{{3,4}}\s*/\s*)*{num}\b"
            rf"|\b{dept}[\s\-_]*{num}(?:\s*/\s*\d{{3,4}})*\b",
            re.IGNORECASE,
        )
    return re.compile(rf"(?<![\d.]){num}(?![\d.])")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def name_score(course_name: str, file_name: str, text: str) -> int:
    """0-100. Exact phrase in the syllabus = 100, else fuzzy on file name / header."""
    n = _norm(course_name)
    if len(n) < 4:
        return 0
    body = _norm(text[:20000])
    if f" {n} " in f" {body} ":
        return 100
    stem = _norm(re.sub(r"\.[a-z0-9]{2,5}$", "", file_name, flags=re.I))
    header = _norm(text[:600])
    return int(max(
        fuzz.token_set_ratio(n, stem),
        fuzz.partial_ratio(n, header) if header else 0,
    ))


# ---- Learning objective extraction --------------------------------------
_HEAD_RE = re.compile(
    r"^\s*(?:[0-9IVX]+[.)]\s*)?(?:student\s+|course\s+|expected\s+)?(?:learning\s+)?"
    r"(?:outcomes?|objectives?|goals?)(?:\s+and\s+(?:outcomes?|objectives?))?\s*:?\s*$"
    r"|^\s*(?:student\s+)?learning\s+(?:outcomes?|objectives?)\b.*$"
    r"|^\s*course\s+(?:learning\s+)?(?:outcomes?|objectives?)\b.*$"
    r"|^.*\b(?:upon|after)\s+(?:successful\s+)?completion\s+of\s+(?:this|the)\s+course\b.*$"
    r"|^.*\bstudents\s+will\s+be\s+able\s+to\b\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_STOP_HEADINGS = (
    "course description", "prerequisite", "textbook", "required text", "required material",
    "course materials", "materials", "grading", "grade", "evaluation", "assessment",
    "course topics", "topics", "course schedule", "schedule", "calendar", "attendance",
    "late work", "makeup", "make-up", "academic integrity", "honor code", "americans with",
    "disabilit", "title ix", "statement on", "campus safety", "mental health", "instructor",
    "office hours", "course policies", "policies", "exams", "assignments", "projects",
    "university policies", "copyright", "communication", "class meeting", "teaching assistant",
)


def _is_stop_heading(line: str) -> bool:
    l = line.strip().lower().rstrip(":")
    if not l or len(l) > 60:
        return False
    if any(l.startswith(h) for h in _STOP_HEADINGS):
        return True
    # A short Title Case line with no verb-ish punctuation looks like a heading
    words = line.strip().rstrip(":").split()
    return (
        1 <= len(words) <= 5
        and not re.search(r"[.;,]$", line.strip())
        and sum(w[:1].isupper() for w in words) == len(words)
        and not re.match(r"^\s*(?:[-*•]|\d+[.)])", line)
    )


def regex_extract_objectives(text: str) -> list[str]:
    if not text:
        return []
    for m in _HEAD_RE.finditer(text):
        tail = text[m.end():]
        head_line = m.group(0)
        items: list[str] = []
        # If the heading line itself carries an objective after a colon, keep it
        after_colon = head_line.split(":", 1)[1].strip() if ":" in head_line else ""
        if len(after_colon) > 15 and not re.search(r"able to\s*$", after_colon, re.I):
            items.append(after_colon)
        for line in tail.splitlines():
            s = line.strip()
            if not s:
                if len(items) >= 2:
                    # blank line after a list usually ends the section,
                    # but keep going if the next bit is still bulleted
                    continue
                continue
            if _is_stop_heading(s):
                break
            if items and not re.match(r"^\s*(?:[-*•●▪–o]|\(?\d+[.)]|\(?[a-z][.)])\s", line) \
                    and not re.match(r"^\s*[A-Z]", s) and items:
                items[-1] += " " + s       # wrapped line
                continue
            if re.match(r"^students?\s+will\s+be\s+able\s+to\s*:?\s*$", s, re.I):
                continue
            items.append(s)
            if len(items) > 25:
                break
        lead_in = re.compile(r"(students?\s+will|able\s+to|completion\s+of)[^:]*:\s*$", re.I)
        items = [re.sub(r"\s+", " ", i).strip() for i in items if len(i) > 8 and not lead_in.search(i)]
        items = [re.sub(r"^(?:[-*•●▪–o]|\(?\d{1,2}[.)]|\(?[a-z][.)])\s+", "", i) for i in items]
        if len(items) >= 1:
            return items
    return []
