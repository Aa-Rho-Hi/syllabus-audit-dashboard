@echo off
REM Double-click: starts the app on this computer and opens the reviewer page. Use the Simple Syllabus tab.
REM Keep this window open while you use it. Close it to stop the app.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv\Scripts\python.exe (
  echo Setting up for the first time, this takes a minute...
  %PY% -m venv .venv || goto nopython
)
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r reviewer\requirements.txt || goto fail
start "" cmd /c "timeout /t 4 >nul & start http://localhost:8000/reviewer"
echo Opening http://localhost:8000/reviewer ... keep this window open.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 || goto fail
exit /b 0
:nopython
echo Python 3 is not installed. Get it from https://www.python.org/downloads/ (tick "Add to PATH"), then run this again.
pause
exit /b 1
:fail
echo.
echo Something went wrong. Scroll up for the message. If it says the port is in use, the app is already running: open http://localhost:8000/reviewer
pause
exit /b 1
