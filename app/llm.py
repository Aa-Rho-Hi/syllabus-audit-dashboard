"""LLM layer. Every function returns None on any failure so callers fall back to regex."""
from __future__ import annotations

import json
import logging
import os
import re

import httpx

from .verbs import ALLOWED, AVOID_PHRASES, AVOID_VERBS

log = logging.getLogger("llm")

PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()          # anthropic | openai
API_KEY = os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("LLM_MODEL") or ("claude-haiku-5-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
BASE_URL = os.getenv("LLM_BASE_URL") or (
    "https://api.anthropic.com" if PROVIDER == "anthropic" else "https://api.openai.com/v1"
)
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "25"))

last_error: str | None = None


def configured() -> bool:
    return bool(API_KEY)


def _call(system: str, user: str, max_tokens: int = 2000) -> str | None:
    global last_error
    if not API_KEY:
        last_error = "No LLM API key set"
        return None
    try:
        if PROVIDER == "anthropic":
            r = httpx.post(
                f"{BASE_URL}/v1/messages",
                headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            out = "".join(b.get("text", "") for b in r.json().get("content", []))
        else:
            r = httpx.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"model": MODEL, "max_tokens": max_tokens, "temperature": 0,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            out = r.json()["choices"][0]["message"]["content"]
        last_error = None
        return out
    except Exception as e:  # network, auth, rate limit, bad JSON...
        last_error = f"{type(e).__name__}: {str(e)[:200]}"
        log.warning("LLM call failed: %s", last_error)
        return None


def _json(text: str | None):
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
def extract_objectives(syllabus_text: str) -> list[str] | None:
    system = ("You extract learning objectives / learning outcomes from course syllabi. "
              "Reply with JSON only.")
    user = (
        "Return the course's learning objectives exactly as written (verbatim, one per item, "
        "without bullets or numbering and without the 'Students will be able to' lead-in). "
        "If the syllabus has none, return an empty list.\n"
        'Format: {"objectives": ["...", "..."]}\n\n'
        f"SYLLABUS:\n{syllabus_text[:18000]}"
    )
    data = _json(_call(system, user))
    if isinstance(data, dict) and isinstance(data.get("objectives"), list):
        return [str(o).strip() for o in data["objectives"] if str(o).strip()]
    return None


def same_course(query_name: str, query_number: str, candidates: list[dict]) -> dict | None:
    """Ask whether any candidate syllabus is the same course despite a different name/number."""
    if not candidates:
        return None
    system = "You compare university course titles. Reply with JSON only."
    lines = "\n".join(f'{i}. file="{c["name"]}" header="{c.get("header", "")[:200]}"'
                      for i, c in enumerate(candidates))
    user = (
        f'A professor entered course number "{query_number}" and name "{query_name}".\n'
        "Which of these existing syllabi (if any) is very likely the SAME course "
        "(same subject matter, possibly reworded title)?\n"
        f"{lines}\n\n"
        'Format: {"same": [indexes], "reason": "short"}'
    )
    data = _json(_call(system, user, 400))
    if isinstance(data, dict) and isinstance(data.get("same"), list):
        return data
    return None


def check_objectives(objectives: list[str]) -> list[dict] | None:
    allowed_by_level: dict[str, list[str]] = {}
    for verb, levels in ALLOWED.items():
        allowed_by_level.setdefault(levels[0], []).append(verb)
    system = (
        "You review course learning objectives against a department's Bloom's Taxonomy verb "
        "policy. Objectives must start with a measurable action verb from the allowed list. "
        "Reply with JSON only."
    )
    user = (
        "ALLOWED VERBS by Bloom level:\n"
        + "\n".join(f"- {lvl}: {', '.join(v)}" for lvl, v in allowed_by_level.items())
        + f"\n\nVERBS TO AVOID: {', '.join(AVOID_VERBS)}"
        + f"\nPHRASES TO AVOID: {', '.join(AVOID_PHRASES)}"
        + "\n\nRules:\n"
          "- Judge only words used as VERBS. Ignore nouns and adjectives even when they match a listed "
          "word, e.g. 'design' in 'RTL design', 'experience' in 'lab experience', 'model' in 'a statistical model'.\n"
          "- Any verb on the ALLOWED list is fine. Never mark an objective 'avoid' or 'not_listed' because "
          "of a verb that is on the ALLOWED list.\n"
          "- 'avoid' = uses a verb or phrase from the avoid lists. 'not_listed' = starts with a verb on neither list. "
          "'vague' = only for genuinely unmeasurable wording that is a synonym of the avoid list "
          "(e.g. 'learn', 'grasp', 'be exposed to', 'gain insight into', 'be aware').\n"
          "- Suggested rewrites must start with an ALLOWED verb and keep the original meaning.\n\n"
          "For each objective return:\n"
          '{"index": n, "leading_verb": "...", "bloom_level": "Knowledge|Comprehension|'
          'Application|Analysis|Synthesis|Evaluation|null", '
          '"status": "ok|avoid|not_listed|vague", '
          '"issues": [{"span": "exact words copied from the objective", "reason": "short"}], '
          '"suggestion": "a rewritten objective using an allowed verb, or null if ok"}\n'
          'Format: {"results": [ ... ]}\n\nOBJECTIVES:\n'
        + "\n".join(f"{i}. {o}" for i, o in enumerate(objectives))
    )
    data = _json(_call(system, user, 3000))
    if not (isinstance(data, dict) and isinstance(data.get("results"), list)):
        return None
    results = data["results"]

    # Enforce the list: a rewrite must start with an allowed verb and contain no avoid-list words.
    bad = [r for r in results if r.get("suggestion") and not suggestion_ok(r["suggestion"])]
    if bad:
        retry_user = (
            "These rewrites broke the rules. Rewrite each so it STARTS with a verb from the ALLOWED list "
            "and uses none of the verbs or phrases to avoid. Keep the meaning.\n"
            f"ALLOWED: {', '.join(sorted(ALLOWED))}\n"
            f"AVOID: {', '.join(AVOID_VERBS + AVOID_PHRASES)}\n"
            'Format: {"fixes": [{"index": n, "suggestion": "..."}]}\n'
            + "\n".join(f'{r.get("index")}. {objectives[r["index"]] if isinstance(r.get("index"), int) and r["index"] < len(objectives) else r["suggestion"]}'
                        for r in bad)
        )
        fixes = _json(_call(system, retry_user, 1500)) or {}
        by_idx = {f.get("index"): f.get("suggestion") for f in fixes.get("fixes", []) if isinstance(f, dict)}
        for r in bad:
            new = by_idx.get(r.get("index"))
            r["suggestion"] = new if new and suggestion_ok(new) else None   # never show an off-list rewrite
    return results


def suggestion_ok(text: str) -> bool:
    from .verbs import AVOID_RE, _lookup_allowed, leading_verb
    verb, _ = leading_verb(text)
    return bool(verb and _lookup_allowed(verb) and not AVOID_RE.search(text))
