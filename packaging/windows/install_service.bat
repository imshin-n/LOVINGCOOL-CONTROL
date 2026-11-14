@echo off
REM lovingcool-control :: packaging\windows\install_service.bat
REM ======================================================
REM Registers lovingcool-control to auto-start at logon and auto-restart if closed
REM (a restart-loop wrapper, see lovingcool-control-loop.bat), via Task Scheduler.
REM
REM NOTE on terminology: this is deliberately NOT a real Windows Service
REM (SCM). Since Windows Vista, services run in Session 0 and cannot show
REM UI on the interactive desktop — a GUI app registered as a literal SCM
REM service would silently never appear on screen. A logon-triggered
REM Scheduled Task running in the user's own session is the correct
REM equivalent for a GUI app that needs to "run like a service" (auto-start,
REM auto-restart, no visible wrapper window) and is what this project uses.
REM
REM Usage:
REM   build_windows.bat                          (build dist\lovingcool-control.exe first)
REM   packaging\windows\install_service.bat

setlocal
set "SCRIPT_DIR=%~dp0"

if not exist "%SCRIPT_DIR%..\..\dist\lovingcool-control.exe" (
    echo [ERROR] dist\lovingcool-control.exe not found - run build_windows.bat first.
    exit /b 1
)

schtasks /create /tn "lovingcool-control" ^
    /tr "wscript.exe //B \"%SCRIPT_DIR%lovingcool-control-loop-hidden.vbs\"" ^
    /sc onlogon /rl limited /f

if errorlevel 1 (
    echo [ERROR] Failed to register the scheduled task.
    exit /b 1
)

echo.
echo Registered - lovingcool-control will auto-start at your next logon and
echo auto-restart automatically if closed or if it crashes.
echo.
echo Start it right now without logging out/in:
echo   schtasks /run /tn "lovingcool-control"
echo Stop (won't auto-restart):
echo   packaging\windows\stop_service.bat
echo Remove entirely:
echo   packaging\windows\uninstall_service.bat
