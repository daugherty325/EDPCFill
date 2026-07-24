@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo AI upscale migration status
echo ===========================
if exist "scryfall_art_cache\upscaled\upscale_migration.pid" (
  set /p UPSCALE_PID=<"scryfall_art_cache\upscaled\upscale_migration.pid"
  echo Migration PID: !UPSCALE_PID!
) else (
  echo No managed migration PID file was found.
)
echo.
powershell.exe -NoProfile -Command "Get-Content -LiteralPath '..\work\ai_upscale_migration.stdout.log' -Tail 20 -ErrorAction SilentlyContinue"
powershell.exe -NoProfile -Command "Get-Content -LiteralPath '..\work\ai_upscale_migration.stderr.log' -Tail 20 -ErrorAction SilentlyContinue"
echo.
pause
