@echo off
cd /d "%~dp0"
if exist "runtime\python.exe" goto ready
if exist ".venv\Scripts\python.exe" if exist "frontend\dist\index.html" goto ready
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
if errorlevel 1 goto failed
:ready
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Please read the diagnostics above.
pause
exit /b 1
