"""Verb lists from the Bloom's Taxonomy handout, plus the regex layer.

The regex layer never needs a network call, so it always works even when the
LLM is down. The frontend builds the same regex from /api/verbs so the
highlighting is instant while the professor types.
"""
from __future__ import annotations

import re

# ---- Straight from the attached PDF -------------------------------------
BLOOMS: dict[str, list[str]] = {
    "Knowledge": [
        "Arrange", "Count", "Define", "Describe", "Draw", "Duplicate", "Enumerate", "Find",
        "Identify", "Label", "List", "Match", "Name", "Order", "Outline", "Quote", "Read",
        "Recall", "Recite", "Record", "Relate", "Repeat", "Reproduce", "Select", "Sequence",
        "State", "Tell", "Underline", "View", "Write",
    ],
    "Comprehension": [
        "Cite", "Classify", "Compare", "Conclude", "Convert", "Defend", "Describe", "Discuss",
        "Distinguish", "Estimate", "Explain", "Express", "Extend", "Generalize", "Give examples",
        "Identify", "Illustrate", "Indicate", "Infer", "Interpret", "Locate", "Make sense of",
        "Paraphrase", "Predict", "Report", "Restate", "Review", "Rewrite", "Select", "Summarize",
        "Tell", "Trace", "Translate",
    ],
    "Application": [
        "Act", "Administer", "Apply", "Articulate", "Change", "Chart", "Collect", "Complete",
        "Compute", "Construct", "Contribute", "Control", "Demonstrate", "Determine", "Develop",
        "Discover", "Dramatize", "Employ", "Establish", "Illustrate", "Imitate", "Implement",
        "Include", "Inform", "Instruct", "Interview", "Manipulate", "Modify", "Operate", "Paint",
        "Participate", "Practice", "Prepare", "Produce", "Provide", "Schedule", "Show", "Sketch",
        "Solve", "Transfer", "Use", "Utilize",
    ],
    "Analysis": [
        "Analyze", "Appraise", "Break down", "Breakdown", "Calculate", "Categorize",
        "Characterize", "Classify", "Compare", "Contrast", "Correlate", "Criticize", "Debate",
        "Deduce", "Diagram", "Differentiate", "Discriminate", "Distinguish", "Examine",
        "Experiment", "Focus", "Identify", "Illustrate", "Infer", "Inspect", "Inventory", "Limit",
        "Model", "Outline", "Point out", "Prioritize", "Question", "Relate", "Research", "Select",
        "Separate", "Subdivide", "Test",
    ],
    "Synthesis": [
        "Adapt", "Anticipate", "Assemble", "Collaborate", "Combine", "Communicate", "Compile",
        "Comply", "Compose", "Create", "Design", "Devise", "Facilitate", "Formulate", "Generate",
        "Incorporate", "Individualize", "Initiate", "Integrate", "Intervene", "Invent", "Make up",
        "Manage", "Model", "Modify", "Negotiate", "Organize", "Perform", "Plan", "Pretend",
        "Progress", "Propose", "Rearrange", "Reconstruct", "Reinforce", "Reorganize", "Set up",
        "Setup", "Structure", "Substitute", "Synthesize", "Validate",
    ],
    "Evaluation": [
        "Appraise", "Argue", "Assess", "Attach", "Choose", "Compare", "Conclude", "Contrast",
        "Criticize", "Critique", "Decide", "Defend", "Describe", "Discriminate", "Estimate",
        "Evaluate", "Explain", "Interpret", "Judge", "Justify", "Measure", "Predict", "Prioritize",
        "Prove", "Rank", "Rate", "Reframe", "Relate", "Revise", "Score", "Select", "Summarize",
        "Support", "Value",
    ],
}

AVOID_VERBS: list[str] = [
    "Believe", "Hear", "Realize", "Capacity", "Intelligence", "Recognize", "Comprehend", "Know",
    "See", "Conceptualize", "Listen", "Self-Actualize", "Memorize", "Think", "Experience",
    "Perceive", "Understand", "Feel",
]

AVOID_PHRASES: list[str] = [
    "Appreciation for", "Acquainted with", "Adjusted to", "Awareness of", "Capable of",
    "Comprehension of", "Cognizant of", "Enjoyment of", "Conscious of", "Familiar with",
    "Interest in", "Interested in", "Knowledge of", "Knowledgeable about", "Understanding of",
]

# Allowed replacements, used when the LLM is unavailable.
SUGGESTIONS: dict[str, list[str]] = {
    "know": ["Define", "Identify", "List"],
    "understand": ["Explain", "Describe", "Interpret"],
    "comprehend": ["Explain", "Paraphrase", "Summarize"],
    "realize": ["Identify", "Distinguish"],
    "recognize": ["Identify", "Select", "Match"],
    "see": ["Identify", "Examine"],
    "hear": ["Identify", "Report"],
    "listen": ["Report", "Summarize"],
    "believe": ["Justify", "Defend", "Argue"],
    "think": ["Analyze", "Evaluate", "Predict"],
    "feel": ["Value", "Judge", "Appraise"],
    "memorize": ["Recall", "Recite", "List"],
    "perceive": ["Distinguish", "Interpret"],
    "conceptualize": ["Design", "Formulate", "Model"],
    "experience": ["Participate", "Practice", "Demonstrate"],
    "self-actualize": ["Plan", "Manage"],
    "capacity": ["Demonstrate", "Apply"],
    "intelligence": ["Demonstrate", "Apply"],
    "appreciation for": ["Value", "Justify"],
    "acquainted with": ["Describe", "Identify"],
    "adjusted to": ["Adapt"],
    "awareness of": ["Identify", "Describe"],
    "capable of": ["Demonstrate", "Apply"],
    "comprehension of": ["Explain", "Summarize"],
    "cognizant of": ["Identify", "Describe"],
    "enjoyment of": ["Value"],
    "conscious of": ["Identify"],
    "familiar with": ["Describe", "Identify"],
    "interest in": ["Value", "Research"],
    "interested in": ["Value", "Research"],
    "knowledge of": ["Define", "Identify"],
    "knowledgeable about": ["Describe", "Explain"],
    "understanding of": ["Explain", "Describe"],
}

_IRREGULAR = {
    "know": ["knows", "knew", "known", "knowing"],
    "understand": ["understands", "understood", "understanding"],
    "see": ["sees", "saw", "seen", "seeing"],
    "think": ["thinks", "thought", "thinking"],
    "feel": ["feels", "felt", "feeling"],
    "hear": ["hears", "heard", "hearing"],
    "capacity": ["capacities"],
    "intelligence": [],
}


def _inflect(word: str) -> list[str]:
    w = word.lower()
    if w in _IRREGULAR:
        forms = [w, *_IRREGULAR[w]]
    elif w.endswith("e"):
        forms = [w, w + "s", w + "d", w[:-1] + "ing"]
    else:
        forms = [w, w + "s", w + "ed", w + "ing"]
    # British spellings (realise, memorise, recognise...)
    forms += [f.replace("iz", "is") for f in forms if "iz" in f]
    # self-actualize also written "self actualize" / "selfactualize"
    forms += [f.replace("-", " ") for f in forms if "-" in f]
    return list(dict.fromkeys(forms))


def avoid_terms() -> list[dict]:
    """Every surface form to flag, each pointing back to its base term."""
    terms: list[dict] = []
    for v in AVOID_VERBS:
        for form in _inflect(v):
            terms.append({"term": form, "base": v.lower(), "kind": "verb"})
    for p in AVOID_PHRASES:
        terms.append({"term": p.lower(), "base": p.lower(), "kind": "phrase"})
    # longest first so "understanding of" wins over "understanding"
    terms.sort(key=lambda t: -len(t["term"]))
    return terms


_TERMS = avoid_terms()
_TERM_LOOKUP = {t["term"]: t for t in _TERMS}
AVOID_RE = re.compile(
    r"\b(" + "|".join(re.escape(t["term"]).replace(r"\ ", r"\s+") for t in _TERMS) + r")\b",
    re.IGNORECASE,
)


def allowed_index() -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for level, verbs in BLOOMS.items():
        for v in verbs:
            idx.setdefault(v.lower(), [])
            if level not in idx[v.lower()]:
                idx[v.lower()].append(level)
    return idx


ALLOWED = allowed_index()

# Lead-ins professors put before the verb ("Students will be able to ...").
_STEM_RE = re.compile(
    r"^\s*(?:[-*•●▪◦·–—\uf0b7\uf0a7>]+|o(?=\s)|\(?[0-9ivxlcIVXLC]{1,4}[.)\]:]|\(?[a-zA-Z][.)\]])?\s*"
    r"(?:(?:upon|after|by the end of)[^,:]*?[,:]\s*)?"
    r"(?:(?:the\s+)?students?\s+(?:will|should|shall|can|must)\s+(?:be\s+able\s+to\s+)?)?"
    r"(?:(?:be\s+)?able\s+to\s+)?(?:swbat\s+)?(?:to\s+)?",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"\n+|(?<=[.;])\s+(?=(?:\d+[.)]|[-*•])\s)")


LEAD_IN_RE = re.compile(
    r"^\s*(?:(?:the\s+)?students?|upon|after|by the end|at the end|on completion)\b.*"
    r"(?:able\s+to|will|should|can)\s*:?\s*$",
    re.IGNORECASE,
)


_BULLET_RE = re.compile(r"^(?:[•●▪◦‣∙·■□–—\uf0b7\uf0a7\uf076\uf0d8\uf0fc*>-]+|o(?=\s))\s*")
_NUMBER_RE = re.compile(r"^\(?(?:\d{1,2}|[ivxIVX]{1,4}|[a-zA-Z])[.)\]:]\s+")


def clean_objective(line: str) -> str:
    """Strip bullets / numbering so the stored text is just the objective."""
    return _NUMBER_RE.sub("", _BULLET_RE.sub("", line.strip()), count=1).strip()


def _verb_start(line: str) -> bool:
    m = re.match(r"[A-Za-z][A-Za-z-]*", line)
    if not m:
        return False
    w = m.group(0).lower()
    return bool(_lookup_allowed(w)) or w in _TERM_LOOKUP


def split_objectives(text: str) -> list[str]:
    """One objective per entry, tolerant of messy pastes: blank lines, tabs, odd spaces,
    Word/PDF bullets, and objectives that wrapped onto several lines."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"[\u00a0\u2007\u202f\t]", " ", raw)
    out: list[str] = []
    for line in _SPLIT_RE.split(raw):
        line = re.sub(r" {2,}", " ", line).strip()
        if not line:
            continue
        marked = bool(_BULLET_RE.match(line) or _NUMBER_RE.match(line))
        prev = out[-1] if out else None
        if (prev and not marked and not prev.endswith(":") and not LEAD_IN_RE.match(line)
                and (re.match(r"[a-z(,;]", line) or (not re.search(r"[.;!?]$", prev) and not _verb_start(line)))):
            out[-1] = prev + " " + line
        else:
            out.append(line)
    return [l for l in out if len(re.sub(r"[^A-Za-z]", "", l)) >= 3 and not LEAD_IN_RE.match(l)]


def _lookup_allowed(word: str) -> tuple[str, list[str]] | None:
    w = word.lower()
    cands = [w]
    if w.endswith("ies"):
        cands.append(w[:-3] + "y")
    if w.endswith("es"):
        cands.append(w[:-2])
    if w.endswith("s"):
        cands.append(w[:-1])
    if w.endswith("ed"):
        cands += [w[:-2], w[:-1]]
    if w.endswith("ing"):
        cands += [w[:-3], w[:-3] + "e"]
    for c in cands:
        if c in ALLOWED:
            return c, ALLOWED[c]
    return None


def leading_verb(objective: str) -> tuple[str, str]:
    """Return (verb, rest) after stripping bullets and 'Students will be able to'."""
    body = _STEM_RE.sub("", objective, count=1).strip()
    m = re.match(r"([A-Za-z][A-Za-z\-]*)(?:\s+(down|out|up|of|examples))?", body)
    if not m:
        return "", body
    one = m.group(1)
    two = f"{one} {m.group(2)}" if m.group(2) else None
    if two and two.lower() in ALLOWED:
        return two, body
    return one, body


# A listed verb right after one of these is being used as a noun ("an understanding",
# "the experience", "lab experience"), so it isn't flagged. Listed phrases are always flagged.
NOUN_BEFORE_RE = re.compile(
    r"\b(?:a|an|the|this|that|these|those|their|its|his|her|our|your|my|any|some|prior|previous|"
    r"hands-on|lab|laboratory|practical|work|industry|field|design|research|learning|user|student|"
    r"good|basic|deep|clear|broad|first-hand|real-world)\s+$", re.IGNORECASE)


def used_as_noun(text: str, start: int, kind: str) -> bool:
    return kind == "verb" and bool(NOUN_BEFORE_RE.search(text[:start]))


def final_status(rx: dict, ai: dict | None) -> str:
    """Combine the pattern check and the AI. The approved list is the authority on verbs:
    the AI can't call an approved verb 'avoid'/'not listed', can't clear a listed avoid word,
    and can't approve a verb that isn't on the list (objective text could try to talk it into that)."""
    status = (ai or {}).get("status") or rx["status"]
    if status not in ("ok", "avoid", "not_listed", "vague"):
        status = rx["status"]
    if rx["flags"] and status == "ok":
        return "avoid"
    if rx["status"] == "not_listed" and status == "ok":
        return "not_listed"
    if not rx["flags"] and rx["status"] == "ok" and status in ("avoid", "not_listed"):
        return "ok"
    return status


def regex_check(text: str) -> list[dict]:
    """Per-objective regex verdicts. Never raises, never calls the network."""
    results = []
    for i, obj in enumerate(split_objectives(text)):
        flags = []
        for m in AVOID_RE.finditer(obj):
            key = re.sub(r"\s+", " ", m.group(0).lower())
            t = _TERM_LOOKUP.get(key, {"base": key, "kind": "verb"})
            if used_as_noun(obj, m.start(), t["kind"]):
                continue
            flags.append({
                "match": m.group(0), "start": m.start(), "end": m.end(),
                "base": t["base"], "kind": t["kind"],
                "suggest": SUGGESTIONS.get(t["base"], []),
            })
        verb, _ = leading_verb(obj)
        hit = _lookup_allowed(verb) if verb else None
        if flags and verb and AVOID_RE.fullmatch(verb):
            status = "avoid"
        elif hit:
            status = "ok" if not flags else "avoid"
        elif flags:
            status = "avoid"
        else:
            status = "not_listed"
        results.append({
            "index": i, "text": obj, "leading_verb": verb,
            "bloom_levels": hit[1] if hit else [],
            "status": status, "flags": flags,
        })
    return results


# ---- text that tries (or happens) to slip past the checks ----
_CONFUSABLES = str.maketrans({
    # Cyrillic / Greek letters that look exactly like Latin ones
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "ɡ": "g", "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w", "А": "A", "В": "B", "Е": "E", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "І": "I", "Ј": "J",
    "Ѕ": "S", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
    "χ": "x", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
    "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
})
_INVISIBLE_RE = re.compile(r"[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e\u200b-\u200f\u202a-\u202e"
                           r"\u2060-\u2064\u2066-\u206f\u3164\ufeff\uffa0]")


def normalize_objective(text: str) -> tuple[str, bool]:
    """Returns (plain text, tampered). Folds full-width letters, look-alike Cyrillic/Greek letters
    and invisible characters back to plain text so 'Undеrstand' (Cyrillic е) is still caught.
    tampered=True when anything like that was found inside a word."""
    import unicodedata
    raw = text or ""
    t = _INVISIBLE_RE.sub("", raw)
    t = unicodedata.normalize("NFKC", t).translate(_CONFUSABLES)
    t = re.sub(r"[ \t\u00a0]+", " ", t).strip()
    hyphen_hit = False
    # 'under-stand', 'fami-liar with': join a hyphenated word only if that reveals a banned term
    for m in reversed(list(re.finditer(r"\b[A-Za-z]+(?:[-\u2010\u2011][A-Za-z]+)+\b", t))):
        joined = re.sub(r"[-\u2010\u2011]", "", m.group(0))
        cand = t[:m.start()] + joined + t[m.end():]
        lo, hi = m.start(), m.start() + len(joined)
        if any(x.start() < hi and x.end() > lo for x in AVOID_RE.finditer(cand)) and \
                not any(x.start() < m.end() and x.end() > m.start() for x in AVOID_RE.finditer(t)):
            t, hyphen_hit = cand, True
    tampered = hyphen_hit or bool(re.search(r"\w[\u00ad\u200b-\u200f\u2060-\u2064\ufeff]+\w", raw)) or \
        any(ch in raw for ch in "аеорсухіјѕԁɡһӏԛԝαεικνορτυχ") and bool(re.search(r"[A-Za-z]", raw))
    return t, tampered
