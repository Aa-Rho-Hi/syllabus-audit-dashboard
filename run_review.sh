#!/usr/bin/env bash
# Mac/Linux version of run_review.bat. Extra options pass through, e.g. ./run_review.sh --term "Spring 2027"
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r reviewer/requirements.txt
.venv/bin/python -m reviewer.pull "$@"
.venv/bin/python -m reviewer.audit --open
