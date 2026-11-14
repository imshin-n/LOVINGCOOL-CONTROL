#!/usr/bin/env bash
# lovingcool-control :: packaging/linux/install_service.sh
# ========================================================
# Installs the commands (lovingcool-controld, lovingcool-ctl, lovingcool-control)
# into ~/.local/bin with `uv tool install`, adds the app to your launcher
# (desktop entry + icon), then installs and starts the systemd --user
# service. Safe to re-run to upgrade after pulling changes.
#
# If the original project's "lovi-ctrl" is installed, its service is stopped
# and disabled (it would fight over the screen); pass --remove-old to also
# delete its unit file, its "LoviCtrl" launcher entry and ~/.local/share/lovi-ctrl.
#
# Usage:
#   packaging/linux/install_service.sh [--remove-old]

set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

REMOVE_OLD=0
[ "${1:-}" = "--remove-old" ] && REMOVE_OLD=1

UNIT_DIR="$HOME/.config/systemd/user"
UNIT=lovingcool-control.service
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
OLD_DESKTOP="$APPS_DIR/lovi-ctrl.desktop"

command -v uv >/dev/null || { echo "[ERROR] uv not found: https://docs.astral.sh/uv/" >&2; exit 1; }

echo "==> Installing commands with uv tool (into ~/.local/bin)"
uv tool install --force --reinstall .

echo "==> Adding the app to your launcher"
install -Dm644 src/lovingcool_control/gui/icon.svg "$ICON_DIR/lovingcool-control.svg"
# Launchers don't always have ~/.local/bin on PATH, so point at it directly.
sed "s|^Exec=.*|Exec=$HOME/.local/bin/lovingcool-control|" packaging/linux/lovingcool-control.desktop |
    install -Dm644 /dev/stdin "$APPS_DIR/lovingcool-control.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" 2>/dev/null || true

if systemctl --user cat lovi-ctrl.service >/dev/null 2>&1 || [ -e "$OLD_DESKTOP" ] || [ -d "$HOME/.local/share/lovi-ctrl" ]; then
    echo "==> Found the original lovi-ctrl"
    systemctl --user disable --now lovi-ctrl.service 2>/dev/null || true
    systemctl --user reset-failed lovi-ctrl.service 2>/dev/null || true
    if [ "$REMOVE_OLD" = 1 ]; then
        rm -f "$UNIT_DIR/lovi-ctrl.service" "$OLD_DESKTOP"
        rm -rf "$HOME/.local/share/lovi-ctrl"
        echo "    removed its service, its LoviCtrl launcher entry and ~/.local/share/lovi-ctrl"
    else
        echo "    service disabled; its LoviCtrl launcher entry still starts the OLD app."
        echo "    Re-run with --remove-old to delete it and its files."
    fi
fi

echo "==> Installing $UNIT"
mkdir -p "$UNIT_DIR"
cp "packaging/linux/$UNIT" "$UNIT_DIR/$UNIT"
systemctl --user daemon-reload
systemctl --user enable "$UNIT"
systemctl --user restart "$UNIT"

sleep 2
echo
systemctl --user --no-pager --lines=5 status "$UNIT" || true
echo
"$HOME/.local/bin/lovingcool-ctl" status || true
echo
echo "Control it:   lovingcool-ctl --help"
echo "Logs:         journalctl --user -u lovingcool-control -f"
echo "Stop:         systemctl --user stop lovingcool-control"
echo "Uninstall:    packaging/linux/uninstall_service.sh"
