@echo off
setlocal
cd /d "%~dp0"

echo This will delete cached PNGs that cached Scryfall metadata identifies as unsupported.
echo Unsupported means non-paper, foreign-language, or The List printings.
echo It will not delete unknown PNGs unless you run the Python script with --delete-unknown.
echo.
choice /M "Delete unsupported cached PNGs now"
if errorlevel 2 exit /b 0

set "BUNDLED_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%BUNDLED_PY%" (
  "%BUNDLED_PY%" clean_nonpaper_scryfall_cache.py --apply
) else (
  py -3 clean_nonpaper_scryfall_cache.py --apply
)

echo.
pause
