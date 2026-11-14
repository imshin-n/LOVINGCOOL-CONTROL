@echo off
REM lovingcool-control :: packaging\windows\uninstall_service.bat
REM ========================================================
REM Removes the scheduled task registered by install_service.bat.
REM Run stop_service.bat first if lovingcool-control is currently running.

schtasks /delete /tn "lovingcool-control" /f
if errorlevel 1 (
    echo [ERROR] Could not remove the scheduled task ^(maybe it wasn't installed?^)
    exit /b 1
)
echo Scheduled task removed.
