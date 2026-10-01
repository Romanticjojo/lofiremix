@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Please finish project setup first.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" -m dj_agent listen "outputs\weeknd-first-listen-20260924\listening" --open
if errorlevel 1 pause
