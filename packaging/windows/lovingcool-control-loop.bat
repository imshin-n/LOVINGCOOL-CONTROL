@echo off
REM lovingcool-control :: packaging\windows\lovingcool-control-loop.bat
REM =====================================================
REM The actual auto-restart loop: runs lovingcool-control.exe, and whenever it
REM exits (window closed, crash, anything) waits 3s and runs it again —
REM forever, until stop_service.bat drops a ".stop" sentinel file here.
REM
REM Not meant to be run directly by a user day-to-day — install_service.bat
REM registers this (via a hidden VBS launcher, see lovingcool-control-loop-hidden.vbs)
REM to run automatically at logon.

title lovingcool-control-service-loop
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "APP_DIR=%SCRIPT_DIR%..\.."
set "EXE=%APP_DIR%\dist\lovingcool-control.exe"
set "STOPFILE=%SCRIPT_DIR%.stop"

if exist "%STOPFILE%" del "%STOPFILE%" >nul 2>&1

if not exist "%EXE%" (
    echo [ERROR] %EXE% not found - run build_windows.bat first.
    exit /b 1
)

:loop
if exist "%STOPFILE%" (
    del "%STOPFILE%" >nul 2>&1
    goto :eof
)

"%EXE%"

if exist "%STOPFILE%" (
    del "%STOPFILE%" >nul 2>&1
    goto :eof
)

timeout /t 3 /nobreak >nul
goto loop
