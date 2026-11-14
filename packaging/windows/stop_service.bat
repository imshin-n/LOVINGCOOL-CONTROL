@echo off
REM lovingcool-control :: packaging\windows\stop_service.bat
REM ===================================================
REM Stops the running instance WITHOUT it auto-restarting. Just closing
REM the app's window does NOT do this — the loop wrapper would relaunch
REM it a few seconds later. This drops a stop sentinel the loop checks
REM for, and force-closes anything currently running so it takes effect
REM immediately rather than waiting for the sentinel to be noticed.

setlocal
set "SCRIPT_DIR=%~dp0"

echo. > "%SCRIPT_DIR%.stop"

taskkill /F /IM lovingcool-control.exe >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq lovingcool-control-service-loop*" >nul 2>&1

echo Stopped. It will not restart until you run:
echo   schtasks /run /tn "lovingcool-control"
echo (or you log off/on again, since it's still registered to start at logon
echo  unless you also run uninstall_service.bat)
