#!/usr/bin/env bash
# lovingcool-control :: build_linux.sh
# =============================
# Builds a standalone single-file Linux executable with PyInstaller.
# Must be run ON Linux — PyInstaller does not cross-compile.
#
# Usage:
#   ./build_linux.sh
#
# Output: dist/lovingcool-control (also copies the udev rule alongside it for convenience)

set -euo pipefail
cd "$(dirname "$0")"

command -v uv >/dev/null || { echo "[ERROR] uv not found: https://docs.astral.sh/uv/" >&2; exit 1; }

echo "==> Installing dependencies (uv)"
uv sync --group build

echo "==> Cleaning previous build artifacts"
rm -rf build dist lovingcool-control.spec

echo "==> Running PyInstaller"
uv run pyinstaller \
    --onefile \
    --windowed \
    --name lovingcool-control \
    --clean \
    --paths src \
    --collect-data lovingcool_control \
    packaging/pyinstaller_entry.py

echo "==> Bundling udev rules alongside the binary"
mkdir -p dist/udev
cp packaging/linux/70-lovingcool-control.rules dist/udev/

echo
echo "==> Done: dist/lovingcool-control"
echo "    Linux users still need the udev rule for non-root USB access:"
echo "      sudo cp dist/udev/70-lovingcool-control.rules /etc/udev/rules.d/"
echo "      sudo udevadm control --reload-rules && sudo udevadm trigger"
