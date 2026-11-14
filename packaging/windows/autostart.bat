@echo off
rem Start LOVINGCOOL-CONTROL when you log in (it runs the screen service inside the app).
rem   autostart.bat install [path\to\lovingcool-control.exe]
rem   autostart.bat remove
rem Uses a per-user scheduled task; no administrator rights needed. Untested on real Windows.
setlocal
set TASK=LOVINGCOOL-CONTROL
if /i "%~1"=="remove" (
    schtasks /delete /tn "%TASK%" /f
    exit /b %errorlevel%
)
if /i not "%~1"=="install" (
    echo usage: %~nx0 install [exe path] ^| remove
    exit /b 2
)
set EXE=%~2
if "%EXE%"=="" set EXE=%~dp0..\..\dist\lovingcool-control.exe
for %%I in ("%EXE%") do set EXE=%%~fI
if not exist "%EXE%" (
    echo Not found: %EXE%  - build it first with build_windows.bat
    exit /b 1
)
schtasks /create /tn "%TASK%" /tr "\"%EXE%\"" /sc onlogon /rl limited /f || exit /b 1
echo Will start at logon: %EXE%
