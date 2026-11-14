#!/usr/bin/env bash
# lovingcool-control :: packaging/linux/install_service.sh
# ===================================================
# Installs lovingcool-control as a systemd --user service (see lovingcool-control.service
# for why it's a user service, not a system one). Restart=always means
# it auto-restarts whenever the app exits — closing the window doesn't
# stop it, only `systemctl --user stop lovingcool-control` does.
#
# Run this from a normal logged-in graphical session (not bare SSH) so
# it can capture DISPLAY/WAYLAND_DISPLAY for the GUI to actually show.
#
# Usage:
#   ./build_linux.sh              (build dist/lovingcool-control first)
#   packaging/linux/install_service.sh

set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

BIN_SRC="dist/lovingcool-control"
INSTALL_DIR="$HOME/.local/share/lovingcool-control"
UNIT_DIR="$HOME/.config/systemd/user"

if [ ! -f "$BIN_SRC" ]; then
    echo "[ERROR] $BIN_SRC not found — run ./build_linux.sh first." >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR" "$UNIT_DIR"
cp "$BIN_SRC" "$INSTALL_DIR/lovingcool-control"
chmod +x "$INSTALL_DIR/lovingcool-control"
cp packaging/linux/lovingcool-control.service "$UNIT_DIR/lovingcool-control.service"

systemctl --user daemon-reload

# Best-effort: make sure systemd --user actually has this session's
# display environment (needed for the GUI to appear). Harmless if it
# fails (e.g. already imported, or running under a DE that does this
# automatically already).
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY 2>/dev/null || true

systemctl --user enable --now lovingcool-control.service

echo
echo "Installed and started."
systemctl --user status lovingcool-control.service --no-pager || true
echo
echo "Stop (won't auto-restart):    systemctl --user stop lovingcool-control"
echo "Resume:                       systemctl --user start lovingcool-control"
echo "Disable auto-start on login:  systemctl --user disable lovingcool-control"
echo "Logs:                         journalctl --user -u lovingcool-control -f"
