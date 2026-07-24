@echo off
setlocal
cd /d "%~dp0"

set "BUNDLED_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%BUNDLED_PY%" (
  "%BUNDLED_PY%" clean_nonpaper_scryfall_cache.py
) else (
  py -3 clean_nonpaper_scryfall_cache.py
)

echo.
pause
