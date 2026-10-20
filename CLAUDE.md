# Syllabus Audit

Checks TAMU course syllabi for learning objectives that use banned Bloom's verbs ("understand", "know", "recognize"...) and suggests rewrites with approved verbs. Two parts:

- **Web app** (`app/`, `static/`): FastAPI. Professors look up a course in a fixed Google Drive folder and check objectives as they type; reviewers audit the whole folder and download an Excel report. Hosted on Google Cloud Run.
- **Simple Syllabus reviewer tool** (`reviewer/`): runs on the reviewer's own computer (Aydin Karsilayan, ECEN). Downloads every syllabus in his Simple Syllabus approval queue, saves a PDF of each, audits them, and opens the same Excel report. Windows: double-click `run_review.bat`. Mac: `./run_review.sh`.

## Commands

```bash
pip install -r reviewer/requirements.txt      # web app only: requirements.txt
python -m pytest -q                            # 141 tests, ~16s. Run before every commit.
python -m reviewer.pull --term "Spring 2027"   # opens a browser for TAMU login, downloads to syllabi/<date>/
python -m reviewer.audit --no-ai --open        # audits the newest syllabi/<date>/ folder
python -m reviewer.pull --logout               # forget the saved login
python -m uvicorn app.main:app --reload --port 8000   # web app; reviewer page has the Simple Syllabus tab
./run_web.sh                                   # same, set up + opens /reviewer (Windows: run_web.bat)
```

## Simple Syllabus API (tamu.simplesyllabus.com)

The documented API (`docs: Simple Syllabus REST API Guide`) is read-only and has **no API keys**, so anything beyond public data needs a real TAMU login (NetID + Duo, can't be automated). Endpoints, all GET:

| Endpoint | Auth | What it gives |
|---|---|---|
| `/api2/doc-public?subject_name=ECEN&statuses[]=awaiting_approval&term_statuses[]=current&term_statuses[]=future&page_size=1000&page=0` | none | The list: code, title, status, term_name, term_id, entity_id, entity_type. Documented. `include[]=components` does **not** return content. |
| `/api2/doc-full-page-get?code=<code>` | none for published | Full rendered syllabus (`items[0].doc_data.components[].html`). Undocumented (it's what their doc page calls). **Only the last published version**, so empty for drafts. |
| `/api2/heading-component?entity_id=<section entity_id>&term_id=<term_id>&family_name=syllabus` | any TAMU login | The current draft, the one the professor submitted. Undocumented (their editor uses it). Each item has `variations[]`; use the one with `variation_status == "passed"`, and its `combined` (name + value HTML). Skip `component_type` internal/quickpick, `is_deleted`, `is_visible is False`. Instructor/material blocks are `rows[].cells[]` keyed to `columns[].heading.id`. |
| `/api/session` | login | Who's logged in (`data.account.entity_id`). |

- **Status words:** Aydin's "To Do" column = `awaiting_approval` (the review modal says "In Progress", don't trust that).
- **Sections vs. courses:** `entity_type: section` are the real syllabi; `course` entries are master templates (skipped by default).
- **Placeholders:** template blocks like `<span data-block-name="section_ca_5">` hold values from `doc_data.properties` (`ca_5` meeting times, `ca_6` room, `course_number`...). Empty ones are dropped.
- Responses include **account IDs and `ca_7` (a UIN)**. Never save them. `slim()` and the heading parser keep only what the audit needs, plus instructor name and email.
- The `doc-public` list is all ECEN syllabi with that status, which can be more than one approver's queue (47 vs. Aydin's 31 on 2026-10-18). It can also miss queue items: his To Do had ECEN 303 503 (56552) on 2026-10-16, which wasn't `awaiting_approval` publicly.
- **His queue (`--mine`, default in `run_review.bat/.sh` and the web tab):** `reviewer/dashboard.py` reads the To Do column off the dashboard page (found by its heading text, not class names; lazy lists get scrolled), takes the `SUBJ NUM SEC (CRN)` labels, and matches them by CRN to `doc-public` with `status=all`. Can't find the column → says so and falls back to `awaiting_approval`. `--crns` is the manual fallback. Dashboard (2026-10-16): Approvals toggle, Term dropdown, View: Columns, columns Not Ready / To Do / Awaiting Approval / Completed, counts capped at 99+. `dashboard-check.json` (no IDs) records what it saw plus the shape of any `dashboard-doc-search` responses, for working out that endpoint later.

## How the reviewer tool fits together

`reviewer/pull.py` → login (`login()`, persistent browser profile in the user's private app-data folder) → `list_docs()` → `fetch_doc()` per syllabus (full-page-get, falls back to heading-component for drafts) → `save_doc()` writes `syllabi/<date>/<term>/_data/<stem>.json` + `<stem>.pdf` → `reviewer/audit.py` reads the JSON, `find_outcomes()` pulls the learning objectives, `app/audit_core.check_items(presplit=True)` checks them, `app/report.build_review_xlsx()` writes the Excel.

**In the web app:** when it runs on your own computer, the reviewer page has a **Simple Syllabus** tab (`app/simplesyllabus_job.py`, `/api/ss/*`). It runs the same `run_pull()` + `run_audit()` as a background job, so the login window opens on that computer. It's switched off on Cloud Run (`K_SERVICE`) and for requests that don't come from the same machine or aren't addressed to `localhost`/`127.0.0.1` (DNS rebinding). POSTs must be JSON so other sites can't send them. **Stop** (`/api/ss/stop`) sets a `threading.Event` that `run_pull()` checks in the login wait, the dashboard read and between syllabi (raises `Stopped`). And `/api/ss/file` only serves PDF/XLSX files inside `syllabi/`. Tests: `tests/test_ss_web.py`.

`reviewer/export_snippet.js` + `reviewer/import_export.py` do the same thing when Python can't reach Simple Syllabus but a logged-in browser can (paste the snippet in DevTools, then import the JSON it saves).

## Rules (don't break these)

- **Syllabus content is untrusted** (professors write it; the server sends it). `tests/test_adversarial.py` covers each attack below. Keep it passing and add a test for any new input path.
  - PDFs: HTML goes through `sanitize_html()` and renders in `render_page()`: JavaScript off, no login cookies, network blocked except `/ui/account-image/` images.
  - Excel: `_no_formulas()` in `app/report.py`. No cell is ever a formula.
  - File names: only `[A-Za-z0-9_]` from `file_stem()`/`safe_folder()`, plus a path-containment check in `save_doc()`.
  - Verb checks run on `normalize_objective()` output (look-alike letters, invisible characters, `under-stand`), and tampering gets flagged.
  - The approved verb list beats the AI: `final_status()` never lets the AI clear an avoid word or approve a verb that isn't on the list.
  - One bad syllabus never stops a run (try/except per document); paging stops at `MAX_PAGES`.
- **Login:** never handle the password. Reuse the browser session only. The profile lives outside the repo, is wiped after 14 days idle, and `--logout` deletes it.
- Never commit `.env`, `syllabi/`, exported JSON files, or anything with instructor data.
- Writing style for anything user-facing (README, report notes, messages): short, casual, plain words, **no em dashes**.
- Aarohi runs commands herself; give her the command rather than driving her screen.

## Known gaps / ideas

- Only works for the ECEN subject by default (`--subject` to change). `--mine` scrapes the dashboard page because `/api2/dashboard-doc-search`'s params are unknown; untested against the live page (only against `tests/test_dashboard.py`'s copy of it).
- No scheduled runs yet (Windows Task Scheduler could run `run_review.bat` with `NOPAUSE=1`).
- Overlap check is O(n²) in objectives; fine for hundreds, slow for tens of thousands.
- `doc-full-page-get`'s `is_missing_required_content` is always true for drafts, so it's ignored for them.
