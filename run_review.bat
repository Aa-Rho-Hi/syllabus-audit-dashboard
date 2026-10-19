@echo off
REM Double-click: downloads your Simple Syllabus review queue, then audits it.
REM Extra options go after the name, e.g.  run_review.bat --term "Spring 2027" --status completed
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv\Scripts\python.exe (
  echo Setting up for the first time, this takes a minute...
  %PY% -m venv .venv || goto nopython
)
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r reviewer\requirements.txt || goto fail
.venv\Scripts\python.exe -m reviewer.pull %* || goto fail
.venv\Scripts\python.exe -m reviewer.audit --open || goto fail
echo.
echo Done.
pause
exit /b 0
:nopython
echo Python 3 is not installed. Get it from https://www.python.org/downloads/ (tick "Add to PATH"), then run this again.
pause
exit /b 1
:fail
echo.
echo Something went wrong. Scroll up for the message.
pause
exit /b 1
