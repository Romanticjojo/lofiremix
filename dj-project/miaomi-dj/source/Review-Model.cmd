@echo off
cd /d "%~dp0"
if not exist "outputs\reference-choice-20260924-v1\acceptance.html" (
  echo Model acceptance file is missing.
  pause
  exit /b 2
)
start "" "outputs\reference-choice-20260924-v1\acceptance.html"
