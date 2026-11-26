@echo off
rem Chem Paper Daily - daily job (collect, select, write introduction, post to Slack).
rem Task Scheduler runs this as "run_daily.bat --scheduled" every morning.
rem Double-click it to run once by hand (for testing); the window then stays open.
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist "%USERPROFILE%\.local\bin" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if not exist "data\logs" mkdir "data\logs"
set "LOG=data\logs\run_daily.log"

if /i not "%~1"=="--scheduled" (
  echo Running the daily job. This can take a few minutes...
  echo Log file: %CD%\%LOG%
)

echo ==== %date% %time% run_daily start ====>> "%LOG%"
uv run python -m app.daily_job >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo ==== %date% %time% run_daily end (exit code %RC%) ====>> "%LOG%"

if /i not "%~1"=="--scheduled" (
  echo.
  echo ---- last lines of the log ----
  powershell -NoProfile -Command "Get-Content -Tail 20 -Encoding UTF8 '%LOG%'"
  echo.
  if "%RC%"=="0" (echo Finished OK.) else (echo Finished with problems - see docs\SETUP_GUIDE.md troubleshooting.)
  pause
)
exit /b %RC%
