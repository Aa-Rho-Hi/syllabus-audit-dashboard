"""Turns a Simple Syllabus doc (the JSON behind /api2/doc-full-page-get) into what the review
needs: section titles, the learning objectives, a printable HTML page and safe file names.
No network calls here, so it's easy to test."""
from __future__ import annotations

import html as htmllib
import re

from bs4 import BeautifulSoup

BASE_URL = "https://tamu.simplesyllabus.com"
OUTCOME_HEAD_RE = re.compile(r"learning\s+(outcome|objective)|course\s+(outcome|objective)|student\s+learning", re.I)
LEAD_IN_RE = re.compile(r"(able\s+to|completion\s+of|students?\s+will)[^.]*:\s*$", re.I)


def slim(doc: dict) -> dict:
    """Keep only what the review needs. Drops editor/approver lists, account ids and other
    people's details that come back in the raw response."""
    term = doc.get("term") if isinstance(doc.get("term"), dict) else {}
    props = doc.get("properties") if isinstance(doc.get("properties"), dict) else {}
    return {
        "code": doc.get("code"),
        "title": doc.get("title") or "",
        "sub_title": doc.get("sub_title") or "",
        "status": doc.get("status"),
        "entity_type": doc.get("entity_type"),
        "term_name": term.get("name") or props.get("ca_10") or "",
        "subject_name": props.get("subject_name") or "",
        "course_number": props.get("course_number") or "",
        "is_missing_required_content": doc.get("is_missing_required_content"),
        "modified": doc.get("modified"),
        "components": [
            {"sort_order": c.get("sort_order"), "component_type": c.get("component_type"),
             "is_public": c.get("is_public"), "html": c.get("html") if isinstance(c.get("html"), str) else ""}
            for c in sorted(_dicts(doc.get("components")),
                            key=lambda c: c.get("sort_order") if isinstance(c.get("sort_order"), (int, float)) else 0)
        ],
    }


def _var(v) -> dict | None:
    """The live version of a block: the variation whose conditions passed, merged (combined)."""
    if not isinstance(v, dict) or not isinstance(v.get("combined"), dict):
        return None
    c = v["combined"]
    if v.get("variation_status") != "passed" or c.get("is_deleted") or c.get("is_visible") is False:
        return None
    return {**c, "name": c.get("name") if isinstance(c.get("name"), str) else "",
            "value": c.get("value") if isinstance(c.get("value"), str) else ""}


def _dicts(x) -> list[dict]:
    return [i for i in x if isinstance(i, dict)] if isinstance(x, list) else []


def _passed(variations: list | None) -> dict | None:
    for v in variations if isinstance(variations, list) else []:
        c = _var(v)
        if c:
            return c
    return None


def fill_placeholders(html: str, props: dict) -> str:
    """Template blocks like <span data-block-name="section_ca_5">Section Meeting Times 1</span>
    get the section's real value; blocks with no value are dropped, like Simple Syllabus does."""
    if "data-block-name" not in (html or ""):
        return html or ""
    soup = BeautifulSoup(html, "html.parser")
    aliases = {"section_name": "name", "course_full_name": "full_name"}
    for span in soup.find_all(attrs={"data-block-name": True}):
        name = span["data-block-name"]
        key = aliases.get(name) or (name if name in props else re.sub(r"^(section|course)_", "", name))
        if key not in props:
            # unknown block still showing its template label ("School Name"): drop it
            if span.get_text(strip=True) == (span.get("data-block-title") or "").strip():
                span.decompose()
            continue
        val = props.get(key)
        if val in (None, ""):
            (span.find_parent(class_="block-container") or span).decompose()
            continue
        span.clear()
        span.append(BeautifulSoup(str(val), "html.parser"))
    return str(soup)


def _rows_html(item: dict) -> str:
    """Instructor / textbook blocks are tables of cells; turn each row into a short list.
    Only the person's name and email are used, never their account details."""
    cols = {(c.get("heading") or {}).get("id"): _var(c.get("variation"))
            for c in _dicts(item.get("columns")) if isinstance(c.get("heading"), dict)}
    out = []
    for row in _dicts(item.get("rows")):
        if row.get("variation") and not _var(row["variation"]):
            continue
        lines = []
        ctx = row.get("context") if isinstance(row.get("context"), dict) else {}
        if ctx.get("full_name"):
            lines.append(f"<p><b>{htmllib.escape(ctx['full_name'])}</b></p>")
            if ctx.get("email"):
                lines.append(f"<p>Email: {htmllib.escape(ctx['email'])}</p>")
        for cell in _dicts(row.get("cells")):
            col, val = cols.get(cell.get("column_id")), _var(cell.get("variation"))
            if not col or not val or not val.get("value"):
                continue
            if ((col.get("meta") or {}).get("column_type")) == "image":
                continue
            lines.append(f"<p><b>{htmllib.escape((col.get('name') or '').strip())}:</b> {val['value']}</p>")
        if lines:
            out.append('<div class="ss-row">' + "".join(lines) + "</div>")
    return "".join(out)


def components_from_headings(items: list[dict], props: dict | None = None) -> list[dict]:
    """Rebuilds a syllabus from /api2/heading-component, which has the latest submitted draft
    (doc-full-page-get only has the last *published* version, so it's empty for new drafts)."""
    props = props or {}
    comps = []
    for n, item in enumerate(_dicts(items)):
        ctype = item.get("component_type")
        if ctype in ("internal", "quickpick"):          # notes to instructors, not syllabus content
            continue
        c = _passed(item.get("variations"))
        if not c:
            continue
        name = (c.get("name") or "").strip()
        body = _rows_html(item) if _dicts(item.get("rows")) else fill_placeholders(c.get("value") or "", props)
        if not BeautifulSoup(body, "html.parser").get_text(strip=True) and not _dicts(item.get("rows")):
            continue                                     # empty optional block
        show_name = name and c.get("is_name_visible") is not False and "(heading hidden)" not in name.lower()
        head = f'<h2 class="component-name">{htmllib.escape(name)}</h2>' if show_name else ""
        if not head and OUTCOME_HEAD_RE.search(name):
            head = f'<h2 class="component-name" style="display:none">{htmllib.escape(name)}</h2>'
        comps.append({"sort_order": c.get("sort_order") if c.get("sort_order") is not None else n,
                      "component_type": ctype, "is_public": True,
                      "html": f'{head}<div class="component-body"><div class="fr-view">{body}</div></div>'})
    return comps


def course_label(doc: dict) -> str:
    """'ECEN 314 200 (38440)' -> 'ECEN 314 200'."""
    t = re.sub(r"\s*\(\d+\)\s*$", "", doc.get("title") or "").strip()
    return t or doc.get("code") or "?"


def file_stem(doc: dict) -> str:
    """Only letters, digits and _ ever reach the file system, whatever the server sends."""
    stem = re.sub(r"[^A-Za-z0-9]+", "_", f"{doc.get('title') or ''}").strip("_")[:80]
    code = re.sub(r"[^A-Za-z0-9]+", "", str(doc.get("code") or ""))[:32]
    return f"{stem or 'syllabus'}_{code or 'nocode'}"


def safe_folder(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9 _-]+", "", str(name or "")).strip(" .")[:80]
    return name or "Unknown term"


def doc_url(doc: dict) -> str:
    return f"{BASE_URL}/en-US/doc/{re.sub(r'[^A-Za-z0-9]+', '', str(doc.get('code') or ''))}"


def sections(doc: dict) -> list[tuple[str, BeautifulSoup]]:
    """[(section title, parsed html)] in page order. Untitled blocks get ''."""
    out = []
    for c in doc.get("components") or []:
        soup = BeautifulSoup(c.get("html") or "", "html.parser")
        h = soup.find(["h2", "h1"], class_=re.compile("component-name")) or soup.find("h2")
        title = re.sub(r"\s+", " ", h.get_text(" ", strip=True)) if h else ""
        out.append((title, soup))
    return out


def _body(soup: BeautifulSoup) -> BeautifulSoup:
    return soup.find(class_="fr-view") or soup.find(class_="component-body") or soup


_BLOCK_TAGS = ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td")


def _own_text(li) -> str:
    """Text of a list item without its nested sub-list. Inline tags are joined with no space,
    so 'Under<b>stand</b>' reads as 'Understand' (like on screen), not 'Under stand'."""
    li = BeautifulSoup(str(li), "html.parser").find("li")
    for sub in li.find_all(["ul", "ol"]):
        sub.decompose()
    for el in li.find_all(_BLOCK_TAGS):
        el.insert_before(" ")
        el.insert_after(" ")
    return re.sub(r"\s+", " ", li.get_text("")).strip()


_JUNK_RE = re.compile(r"[\ue000-\uf8ff\u200b-\u200f\u2060-\u2064\ufeff\u00ad]")  # symbol-font bullets, zero-width, soft hyphen
_BULLET_SPLIT_RE = re.compile(r"\s*[•●▪◦‣∙■□]\s*")


def _clean(text: str) -> str:
    text = re.sub(r"(?<=\w)[\u200b-\u200f\u2060-\u2064\ufeff\u00ad]+(?=\w)", "", text or "")   # 'Under\u200bstand'
    return re.sub(r"\s+", " ", _JUNK_RE.sub(" ", text)).strip()


def _lead_in(line: str) -> bool:
    return len(line) < 400 and bool(LEAD_IN_RE.search(line))      # long lines can't be lead-ins; keeps regex fast


def _blocks(body) -> list[str]:
    """Paragraph-level lines. Inline tags (span, em, b) stay inside their line; <br> breaks it."""
    for br in body.find_all("br"):
        br.replace_with("\n")
    out = []
    for el in body.find_all(["p", "div", "h3", "h4", "h5", "td"]):
        if el.find(["p", "div", "h3", "h4", "h5", "td", "ul", "ol"]):
            continue
        out += el.get_text(" ").split("\n")
    if not out:
        out = body.get_text(" ").split("\n")
    return out


def _split_inline_bullets(items: list[str]) -> list[str]:
    """'• Apply X • Design Y' typed into one line becomes two objectives."""
    out = []
    for i in items:
        parts = [p for p in _BULLET_SPLIT_RE.split(i) if p.strip()]
        out += parts if len(parts) > 1 or i.lstrip()[:1] in "•●▪◦‣∙■□" else [i]
    return out


def _finish(items: list[str]) -> list[str]:
    items = [_clean(i) for i in _split_inline_bullets(items)]
    # "...signals and systems; and to" -> "...signals and systems"
    items = [re.sub(r"\s*[;,]\s*(?:and|or)(?:\s+to)?\s*$|\s+and\s+to\s*$|\s*;\s*$", "", i) for i in items]
    return [i for i in items if len(re.sub(r"[^A-Za-z]", "", i)) >= 8 and not _lead_in(i)
            and not _is_subheading(i)]


def _is_subheading(i: str) -> bool:
    """'Societal Impact', 'Project Management': group labels inside the list, not objectives."""
    words = i.rstrip(":").split()
    return len(words) <= 3 and all(w[:1].isupper() for w in words) and not i.endswith(".")


def _section_objectives(soup, require_lead_in: bool = False) -> list[str]:
    body = _body(soup)
    lines = [_clean(l) for l in _blocks(BeautifulSoup(str(body), "html.parser"))]
    lines = [l for l in lines if l]
    lead = [i for i, l in enumerate(lines) if _lead_in(l)]
    if require_lead_in and not lead:
        return []
    items = _finish([_own_text(li) for li in body.find_all("li")])
    if items:
        return items
    return _finish(lines[lead[-1] + 1:] if lead else lines)


def find_outcomes(doc: dict) -> tuple[list[str], str, bool]:
    """(objectives, section title, renamed). Looks for the standard outcomes heading first. If a
    professor renamed it ("What you'll get out of this class"), falls back to any section that
    says "...will be able to:" and lists things after it, and reports that it was renamed."""
    secs = sections(doc)
    for title, soup in secs:
        if OUTCOME_HEAD_RE.search(title):
            return _section_objectives(soup), title, False
    for title, soup in secs:
        items = _section_objectives(soup, require_lead_in=True)
        if items:
            return items, title or "(untitled section)", True
    return [], "", False


def objectives(doc: dict) -> list[str]:
    """Learning objectives as a list of strings (see find_outcomes)."""
    return find_outcomes(doc)[0]


def has_outcomes_section(doc: dict) -> bool:
    return any(OUTCOME_HEAD_RE.search(t) for t, _ in sections(doc))


PRINT_CSS = """
@page { size: Letter; margin: 0.6in 0.6in 0.7in; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 10.5pt; color: #222; line-height: 1.4; }
.ss-head { border-bottom: 3px solid #500000; padding-bottom: 6px; margin-bottom: 14px; }
.ss-head h1 { color: #500000; font-size: 18pt; margin: 0; }
.ss-head .meta { color: #555; font-size: 9pt; margin-top: 3px; }
.ss-draft { background: #fff3d4; border: 1px solid #e0b84c; padding: 6px 10px; font-size: 9pt; margin-bottom: 12px; }
h1 { font-size: 16pt; } h2 { color: #500000; font-size: 13pt; border-bottom: 1px solid #ccc; padding-bottom: 2px;
     margin: 16px 0 6px; break-after: avoid; }
img { max-width: 140px; max-height: 140px; }
[style*="flex"] { display: block !important; max-width: none !important; }
.heading-cells-container > div { display: inline-block; vertical-align: top; margin-right: 18px; }
p { margin: 3px 0; } ul, ol { margin: 3px 0 3px 22px; padding: 0; }
table { border-collapse: collapse; } td, th { border: 1px solid #ccc; padding: 3px 6px; }
"""


_DANGEROUS_TAGS = ["script", "iframe", "frame", "object", "embed", "meta", "link", "base", "form", "input",
                   "button", "textarea", "select", "svg", "math", "applet", "audio", "video", "source", "noscript"]


def sanitize_html(html: str) -> str:
    """Professor-written HTML, made safe to render: no scripts, frames, forms, redirects,
    event handlers or javascript: links. Images only from Simple Syllabus itself."""
    soup = BeautifulSoup(html or "", "html.parser")
    for t in soup.find_all(_DANGEROUS_TAGS):
        t.decompose()
    for t in soup.find_all(True):
        for attr in list(t.attrs):
            val = " ".join(t[attr]) if isinstance(t[attr], list) else str(t[attr])
            if attr.lower().startswith("on") or attr.lower() in ("srcset", "formaction", "background", "ping"):
                del t[attr]
            elif attr.lower() in ("href", "src", "action", "xlink:href") and re.match(r"\s*(javascript|vbscript|data|file):", val, re.I):
                del t[attr]
            elif attr.lower() == "style" and re.search(r"url\s*\(|expression\s*\(|@import", val, re.I):
                del t[attr]
        if t.name == "img" and not str(t.get("src", "")).startswith(("/ui/account-image/", f"{BASE_URL}/ui/account-image/")):
            t.decompose()
    return str(soup)


def printable_html(doc: dict) -> str:
    """A clean, self-contained page for PDF output. <base> lets instructor photos and logos load
    from Simple Syllabus with the reviewer's own login."""
    status = (doc.get("status") or "").replace("_", " ")
    banner = ""
    if doc.get("status") != "completed":
        banner = (f'<div class="ss-draft">Status in Simple Syllabus: <b>{htmllib.escape(status)}</b>. '
                  f'This copy was downloaded for review and may change before it is published.</div>')
    body = "\n".join(sanitize_html(c.get("html") or "") for c in doc.get("components") or [])
    title = htmllib.escape(f"{doc.get('title', '')} {doc.get('sub_title', '')}".strip())
    return f"""<!doctype html><html><head><meta charset="utf-8"><base href="{BASE_URL}/">
<title>{title}</title><style>{PRINT_CSS}</style></head><body>
<div class="ss-head"><h1>{title}</h1><div class="meta">{htmllib.escape(doc.get('term_name', ''))} ·
{htmllib.escape(doc_url(doc))}</div></div>{banner}
{body}
</body></html>"""
