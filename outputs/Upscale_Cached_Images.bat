@echo off
setlocal
cd /d "%~dp0"

echo AI-upscaling cached images with resumable progress.
echo Originals are preserved. Press Ctrl+C to stop; run this file again to resume.
echo.

set "BUNDLED_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%BUNDLED_PY%" (
  "%BUNDLED_PY%" -B upscale_cached_images.py --batch-size 32
) else (
  py -3 -B upscale_cached_images.py --batch-size 32
)

echo.
pause
