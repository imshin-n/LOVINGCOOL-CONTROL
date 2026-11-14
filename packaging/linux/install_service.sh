#!/usr/bin/env bash
# lovingcool-control :: packaging/linux/install_service.sh
# ========================================================
# Installs the commands (lovingcool-controld, lovingcool-ctl, lovingcool-control)
# into ~/.local/bin with `uv tool install`, then installs and starts the
# systemd --user service. Safe to re-run to upgrade after pulling changes.
#
# If the old "lovi-ctrl" service from the original project is installed, it
# is stopped and disabled (it would fight over the screen); pass
# --remove-old to also delete its unit file and ~/.local/share/lovi-ctrl.
#
# Usage:
#   packaging/linux/install_service.sh [--remove-old]

set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

REMOVE_OLD=0
[ "${1:-}" = "--remove-old" ] && REMOVE_OLD=1

UNIT_DIR="$HOME/.config/systemd/user"
UNIT=lovingcool-control.service

command -v uv >/dev/null || { echo "[ERROR] uv not found: https://docs.astral.sh/uv/" >&2; exit 1; }

echo "==> Installing commands with uv tool (into ~/.local/bin)"
uv tool install --force --reinstall .

if systemctl --user cat lovi-ctrl.service >/dev/null 2>&1; then
    echo "==> Disabling the old lovi-ctrl service"
    systemctl --user disable --now lovi-ctrl.service 2>/dev/null || true
    systemctl --user reset-failed lovi-ctrl.service 2>/dev/null || true
    if [ "$REMOVE_OLD" = 1 ]; then
        rm -f "$UNIT_DIR/lovi-ctrl.service"
        rm -rf "$HOME/.local/share/lovi-ctrl"
        echo "    removed its unit file and ~/.local/share/lovi-ctrl"
    else
        echo "    (left its files; re-run with --remove-old to delete them)"
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
