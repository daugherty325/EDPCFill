@echo off
setlocal
cd /d "%~dp0"

set "PID_FILE=scryfall_art_cache\upscaled\upscale_migration.pid"
if not exist "%PID_FILE%" (
  echo No managed AI upscale migration is running.
  pause
  exit /b 0
)

set /p UPSCALE_PID=<"%PID_FILE%"
echo Stopping AI upscale migration PID %UPSCALE_PID%...
taskkill /PID %UPSCALE_PID% /F
del /Q "%PID_FILE%" 2>nul
echo Completed images were preserved. Double-click Upscale_Cached_Images.bat to resume.
echo.
pause
