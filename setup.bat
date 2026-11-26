@echo off
rem Chem Paper Daily - first-time setup. Double-click this file.
rem The real work is in scripts\setup.ps1 (PowerShell handles Japanese text reliably).
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
echo.
echo Press any key to close this window.
pause >nul
