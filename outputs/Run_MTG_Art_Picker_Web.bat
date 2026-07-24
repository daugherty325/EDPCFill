@echo off
setlocal
cd /d "%~dp0"

set "BUNDLED_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%BUNDLED_PY%" (
  "%BUNDLED_PY%" mtg_art_picker_web.py
) else (
  py -3 mtg_art_picker_web.py
)

if errorlevel 1 (
  echo.
  echo The web app did not start.
  echo.
  pause
)
