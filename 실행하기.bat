@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
set "result=%errorlevel%"
echo.
pause
exit /b %result%
