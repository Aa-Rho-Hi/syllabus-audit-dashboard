# Syllabus Audit

Demo: [docs/demo.mp4](docs/demo.mp4)

Professor types a course number (and optionally a name). The app searches the syllabus folder in Google Drive.

- **Course exists** → shows its learning objectives, with avoid-list words in red.
- **Same name, different number** → warns them, shows that course's objectives, still lets them write new ones.
- **Not found** → objectives box. Avoid-list verbs turn red as they type; AI review adds rewrites using approved verbs.

**No sign-in for faculty.** On Cloud Run the app reads one department Drive folder itself (set `DRIVE_FOLDER_LINK`). Faculty just open the link and type a course number (`ECEN 314` or `ECEN314`, both work).

**Faculty:** existing course → objectives appear with a **Copy objectives** button for Simple Syllabus. New or revised → paste objectives, problem verbs turn red, **Fix all** rewrites them with allowed verbs.

**Coordinator:** the "Coordinator: review all courses" tab checks every syllabus in the folder and downloads an **Excel file**: each objective, the problem found, a suggested rewrite (allowed verbs only), **overlaps** (objectives that are the same or nearly the same in another course), and a **Status** dropdown (Pending / Approved / Needs changes) plus a comment column. The Summary sheet has a polite, ready-to-send **note to faculty** for each course that needs changes, and the Overlaps sheet lists every matching pair. Nothing is stored by the app.

Extras:
- **Fix all**: rewrites every flagged objective in one click (AI rewrites; without AI it swaps avoid-list verbs like Understand → Explain). Undo is right there.

## Regex + LLM, so one failing doesn't break it

| Job | Regex layer (always on) | LLM layer (when a key is set) | If the LLM fails |
|---|---|---|---|
| Find the course | Course number regex (`CSCE 489`, `csce-489`, `CSCE 489/689`) on file names + file text, fuzzy name match | Decides if a "similar name" syllabus is really the same course | Fuzzy score alone decides |
| Pull objectives from a syllabus | Finds the "Learning Outcomes / Objectives" heading and reads the list until the next section | Extracts them verbatim | Regex result is used |
| Check verbs | Red highlight in the browser on every keystroke, catches inflections (understood, knows, recognise) and avoid phrases (knowledge of, familiar with) | Catches vague wording the list misses ("learn", "gain familiarity"), labels Bloom level, writes a fixed version | Red highlights + amber "verb not on approved list" + suggested verbs |

Regex is the floor: if regex says a word is on the avoid list, the AI can't mark that objective "ok".

## Run it

```bash
cd syllabus-audit
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env
python -m uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. Leave the folder link empty to try the built-in samples (`CSCE 689`, `ECEN 403`, or number `CSCE 999` + name `Software Engineering`).

Tests: `pytest -q`

## Connect Google Drive (one time, ~5 min)

Professors paste any Drive folder link into the app and click **Sign in with Google**. The app then reads that folder (and its subfolders) with *their* read-only access, so "Shared with me" folders work and nothing has to be downloaded.

1. Go to https://console.cloud.google.com and create or pick a project.
2. **APIs & Services → Library** → search **Google Drive API** → **Enable**.
3. **APIs & Services → OAuth consent screen** (or **Google Auth Platform**):
   - User type: **Internal** if your Google account is a TAMU Workspace account and that option shows up (anyone at TAMU can then sign in). Otherwise **External**.
   - App name "Syllabus Audit", your email for support and developer contact.
   - Scopes: add `.../auth/drive.readonly`.
   - If External: under **Test users**, add the Gmail/TAMU addresses of everyone who'll use it (up to 100).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**
   - Authorized JavaScript origins: `http://localhost:8000` (add your real URL too once you host it)
   - No redirect URI needed.
5. Copy the **Client ID** (ends in `.apps.googleusercontent.com`) into `.env` as `GOOGLE_CLIENT_ID=...` and restart.

Sign-in lasts an hour, after which the app asks again. The token stays in the browser tab and is only used to read Drive; the app never stores it on disk.

Reads PDFs, Word (.docx), Google Docs and .txt, follows subfolders and shortcuts. Text is cached in `.cache/` and only re-read when a file changes. The first lookup in a big folder uses Drive's own search while the rest gets read in the background.

Optional fallbacks if you don't want sign-in: a service account (`GOOGLE_APPLICATION_CREDENTIALS`, share the folder with its email) or an API key (`GOOGLE_API_KEY`, folder must be "Anyone with the link"). The app tries sign-in first, then these.

## Turn on AI

Put your key in `.env` as `LLM_API_KEY=...` (Anthropic by default). For OpenAI or any OpenAI-compatible API, also set `LLM_PROVIDER=openai` (and `LLM_BASE_URL` if not OpenAI). Restart; header shows `AI: on`.

Never commit `.env` or `service-account.json`.

## Host it on Google Cloud Run

Use the same Google Cloud project as your OAuth Client ID. You need the [gcloud CLI](https://cloud.google.com/sdk/docs/install) and `gcloud auth login` done once.

```bash
cd syllabus-audit
PROJECT_ID=your-project-id
REGION=us-central1
gcloud config set project $PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com

# Store the LLM key as a secret (never as a plain env var)
printf %s "PASTE-YOUR-LLM-KEY" | gcloud secrets create llm-api-key --data-file=-
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding llm-api-key \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Build and deploy (Cloud Build uses the Dockerfile)
gcloud run deploy syllabus-audit --source . --region $REGION \
  --allow-unauthenticated --max-instances 1 --no-cpu-throttling --memory 1Gi \
  --set-env-vars "LLM_PROVIDER=openai,GOOGLE_CLIENT_ID=YOUR-CLIENT-ID.apps.googleusercontent.com" \
  --set-secrets "LLM_API_KEY=llm-api-key:latest"
```

### Department folder

The app reads the folder with the Cloud Run service's own Google identity, so no key files and no sign-in for faculty. Find its email:

```bash
gcloud run services describe syllabus-audit-dashboard --region us-central1 --format="value(spec.template.spec.serviceAccountName)"
# blank means the default: PROJECT_NUMBER-compute@developer.gserviceaccount.com
```

1. In Google Drive, **Share** the syllabus folder with that email as **Viewer**.
2. `gcloud services enable drive.googleapis.com`
3. `gcloud run services update syllabus-audit-dashboard --region us-central1 --update-env-vars "DRIVE_FOLDER_LINK=https://drive.google.com/drive/folders/FOLDER_ID"`

If you used Google sign-in instead (no `DRIVE_FOLDER_LINK`):
1. Copy the `https://syllabus-audit-....run.app` URL it prints.
2. Google Cloud Console → **APIs & Services → Credentials** → your OAuth client → add that URL under **Authorized JavaScript origins** (no trailing slash). Takes a few minutes to kick in.
3. Optional: pre-fill the department folder for everyone: `gcloud run services update syllabus-audit --region $REGION --update-env-vars DRIVE_FOLDER_LINK=https://drive.google.com/drive/folders/...`

Why those flags:
- `--max-instances 1` and one worker: folder audits run in the background and the page polls the same instance for progress.
- `--no-cpu-throttling`: keeps the background audit running between polls.
- Drive data is only readable by someone who signs in with an account that can see the folder, so `--allow-unauthenticated` exposes the page, not the syllabi. It does let anyone call the AI check with your key; put it behind IAP or `--no-allow-unauthenticated` if that matters.

To ship changes later, run the same `gcloud run deploy` command again.

## Files

```
app/verbs.py     Verb lists from the Bloom's handout + regex checker
app/matcher.py   Course number/name matching + objective extraction regex
app/llm.py       LLM calls (return None on any failure)
app/sources.py   Drive folder links, sign-in / service account / API key access, text cache
app/main.py      API: /api/folder, /api/lookup, /api/check, /api/audit, /api/audit/{id}/excel, /api/verbs, /api/config
app/report.py    Builds the coordinator's Excel review file + faculty notes
app/overlap.py   Finds near-identical objectives across courses
static/index.html  The whole UI
```

To change the allowed/avoid lists, edit `BLOOMS`, `AVOID_VERBS`, `AVOID_PHRASES` in `app/verbs.py`. The browser picks them up from `/api/verbs`.
