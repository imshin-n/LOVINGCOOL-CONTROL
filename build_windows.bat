@echo off
REM lovingcool-control :: build_windows.bat
REM =================================
REM Builds a standalone single-file Windows executable with PyInstaller.
REM Must be run ON Windows — PyInstaller does not cross-compile.
REM
REM Usage:
REM   build_windows.bat
REM
REM Output: dist\lovingcool-control.exe (udev\ rules are Linux-only, not copied here)

setlocal enabledelayedexpansion
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv not found: https://docs.astral.sh/uv/
    exit /b 1
)

echo ==^> Installing dependencies (uv)
uv sync --group build
if errorlevel 1 (
    echo Dependency install failed.
    exit /b 1
)

echo ==^> Cleaning previous build artifacts
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist lovingcool-control.spec del /q lovingcool-control.spec

echo ==^> Running PyInstaller
uv run pyinstaller ^
    --onefile ^
    --windowed ^
    --name lovingcool-control ^
    --clean ^
    --paths src ^
    packaging\pyinstaller_entry.py
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo ==^> Done: dist\lovingcool-control.exe
echo     If Windows Defender/SmartScreen flags the exe, that's a common
echo     false-positive for unsigned PyInstaller binaries — see README.
