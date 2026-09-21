@echo off
rem ----------------------------------------------------------------
rem  ALM issue export launcher.
rem  Double-click this file, or pass options through:
rem      run.bat -id VP-6955
rem      run.bat -query "status:(in_progress)"
rem ----------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
echo.
pause
