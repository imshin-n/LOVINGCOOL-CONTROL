@echo off
rem Build a single-file Windows exe of the GUI at dist\lovingcool-control.exe (PyInstaller).
rem Untested on real Windows hardware.
setlocal
cd /d "%~dp0"
where uv >nul 2>nul || (echo uv is required: https://docs.astral.sh/uv/ & exit /b 1)

uv sync --group build || exit /b 1
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
uv run pyinstaller --onefile --windowed --clean --name lovingcool-control ^
    --paths src --collect-data lovingcool_control packaging\pyinstaller_entry.py || exit /b 1

echo Built dist\lovingcool-control.exe
echo To start it at logon: packaging\windows\autostart.bat install
