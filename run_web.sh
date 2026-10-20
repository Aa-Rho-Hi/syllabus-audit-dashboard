#!/usr/bin/env bash
# Mac/Linux version of run_web.bat: starts the app and opens the reviewer page. Ctrl+C to stop.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r reviewer/requirements.txt
URL=http://localhost:8000/reviewer
if [ "$(uname)" = Darwin ]; then
  (sleep 3; open "$URL") &
else
  # Linux often has no Chrome or Edge; then use Playwright's own Chromium (downloaded once, ~150 MB).
  command -v google-chrome >/dev/null || command -v microsoft-edge >/dev/null || .venv/bin/python -m playwright install chromium
  (sleep 3; xdg-open "$URL" >/dev/null 2>&1 || echo "Open $URL in your browser.") &
fi
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
