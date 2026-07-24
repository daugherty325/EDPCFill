@echo off
setlocal
cd /d "%~dp0"

set "BUNDLED_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%BUNDLED_PY%" (
  "%BUNDLED_PY%" mtg_art_picker.py
) else (
  py -3 mtg_art_picker.py
)

if errorlevel 1 (
  echo.
  echo The app did not start. If the error mentions Pillow, run:
  echo py -3 -m pip install pillow
  echo.
  pause
)
