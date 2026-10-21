# Syllabus Audit

Checks TAMU course syllabi for learning objectives that use banned Bloom's verbs ("understand", "know", "recognize"...) and suggests rewrites with approved verbs. Two parts:

- **Web app** (`app/`, `static/`): FastAPI. Professors look up a course in a fixed Google Drive folder and check objectives as they type; reviewers audit the whole folder and download an Excel report. Hosted on Google Cloud Run.
- **Simple Syllabus reviewer tool** (`reviewer/`): runs on the reviewer's own computer (Aydin Karsilayan, ECEN). Downloads every syllabus in his Simple Syllabus approval queue, saves a PDF of each, audits them, and opens the same Excel report. Windows: double-click `run_review.bat`. Mac: `./run_review.sh`.

## Commands

```bash
pip install -r reviewer/requirements.txt      # web app only: requirements.txt
python -m pytest -q                            # 162 tests, ~3 min (the dashboard ones drive a fake Simple Syllabus in Chromium). Run before every commit.
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
- **His queue (`--mine`, default in `run_review.bat/.sh` and the web tab):** `reviewer/dashboard.py` reads the To Do column off the dashboard page. Photos (2026-10-20): toggle All (54) / Syllabus / **Approvals (42, what he uses)**; **Term is a multi-select** (Spring 2027 CS/Galveston/Qatar, Fall 2026..., all ticked) whose box shows only "Spring 2027 - College ..."; columns Not Ready 99+ / To Do / Awaiting Approval (collapsed, vertical) / Completed 99+; each card shows **its own term** + `SUBJ NUM SEC (CRN)`; loading all cards can take ~10 min, and **the To Do count climbs as cards arrive**, so nobody can tell when it's done by looking. The first version only got 2: it clicked Approvals, waited 2 s, and filtered by the cut-off term box.
  - **Guided (default):** after login the tool waits on the dashboard; he sets Approvals/terms and clicks **Read my To Do column** (web) or presses Enter (CLI) right away. `--auto` (or no terminal) picks Approvals itself.
  - Then it keeps scrolling (its own scroll box or the page, virtualized or not, nudging at the bottom for infinite scroll) and reading each card with its term until the column has **settled**: at the bottom, no change in count/cards for `quiet_s` (45 s), no spinner / progress ring / `aria-busy` / open xhr (ignored after `stuck_s` 300 s of no change), and at least the count read. Waits 3x when "You have N tasks" > the column's count, 2x when fewer cards than the count were read. 30 min cap. Toggle changes or the count drops mid-read → starts over. Known limit: a stall longer than the quiet window with none of those signals ends the read early (the re-check below catches it if it loads during the download).
  - **Strictly To Do:** cards are only read inside the To Do column box (grown from its heading until the box would include another column's heading). It also reads Not Ready / Awaiting Approval / Completed, and any card (same label + term) also shown there is left out and listed (`excluded`). A card only matches a doc in the same term, even when there's one candidate, and docs with status `completed` / `not_started` are left out with the reason. If the column says N but no cards could be read from it on its own, it stops with a problem instead of saying "empty".
  - **Re-check:** after the downloads, it reads the column again without reloading (up to 3 passes) and downloads cards that weren't there the first time (late loads, or tasks added while it ran if the page updates live). `added_later` in `dashboard-check.json`. `--no-recheck` turns it off. Anything added after the run ends: run it again. Matching: label + the card's term against `doc-public status=all` (a CRN can repeat across terms; never guesses between terms). Cards missing from `doc-public` or never drawn come from the dashboard's own API responses (watched from the first page load), but a list only fills in cards if it has exactly the column's count and includes every card read. Codes/IDs from it must match `[A-Za-z0-9_-]`.
  - Can't find the column → says so and stops. It **never** swaps in the whole ECEN list. Short read → "Heads up: only X of N". `--crns` is the manual fallback. `dashboard-check.json` (no IDs) has the cards read, count, toggle, matches, missing, and the shape of dashboard API responses.
  - Tests: `tests/fake_ss.py` is a fake tamu.simplesyllabus.com built from the photos; `tests/test_dashboard.py` runs `run_pull()` + `run_audit()` end to end against it (guided, auto, climbing count, infinite scroll, stalls, stuck spinner, filters changed mid-read, cards also in Completed / Not Ready, tasks added mid-run, never-drawn cards, spoofed headers, hostile API data). Tests pass `step_ms=150` and short `quiet_s` to stay fast.

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

- Only works for the ECEN subject by default (`--subject` to change). `--mine` reads the dashboard page because the dashboard API's params are unknown; tested against `tests/fake_ss.py`, not yet against his live page.
- No scheduled runs yet (Windows Task Scheduler could run `run_review.bat` with `NOPAUSE=1`).
- Overlap check is O(n²) in objectives; fine for hundreds, slow for tens of thousands.
- `doc-full-page-get`'s `is_missing_required_content` is always true for drafts, so it's ignored for them.
