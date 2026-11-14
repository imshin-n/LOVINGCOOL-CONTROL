#!/usr/bin/env bash
# Build a single-file Linux binary of the GUI at dist/lovingcool-control (PyInstaller).
# Most people want packaging/linux/install_service.sh or the Arch PKGBUILD instead.
set -euo pipefail
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }

uv sync --group build
rm -rf build dist ./*.spec
uv run pyinstaller --onefile --windowed --clean --name lovingcool-control \
    --paths src --collect-data lovingcool_control packaging/pyinstaller_entry.py
install -Dm644 packaging/linux/70-lovingcool-control.rules dist/udev/70-lovingcool-control.rules

echo "Built dist/lovingcool-control"
echo "For access without root, install the udev rule:"
echo "  sudo install -Dm644 dist/udev/70-lovingcool-control.rules /etc/udev/rules.d/70-lovingcool-control.rules"
