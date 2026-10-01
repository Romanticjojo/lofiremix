@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Project Python environment is missing.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" -m dj_agent sample-view "outputs\weeknd-20min-20260925-v4" --open
if errorlevel 1 pause
