@echo off
rem Chem Paper Daily - start the web app and open http://127.0.0.1:8765 in the browser.
rem Closing this window stops the app.
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv was not found. Please double-click setup.bat first.
  pause
  exit /b 1
)
echo Starting Chem Paper Daily at http://127.0.0.1:8765 ...
echo The browser opens automatically. Close this window to stop the app.
uv run python -m app.web.main --open-browser
if errorlevel 1 (
  echo.
  echo The app stopped with an error. See the message above.
  pause
)
