#!/usr/bin/env bash
# lovingcool-control :: packaging/linux/uninstall_service.sh
# Stops and removes the systemd --user service and the uv-installed commands.
# Your saved state (~/.config/lovingcool-control/) is left in place.
set -euo pipefail

UNIT_DIR="$HOME/.config/systemd/user"

systemctl --user disable --now lovingcool-control.service 2>/dev/null || true
rm -f "$UNIT_DIR/lovingcool-control.service"
systemctl --user daemon-reload
uv tool uninstall lovingcool-control 2>/dev/null || true

echo "Service and commands removed."
echo "Saved settings kept in ~/.config/lovingcool-control/ (delete it to forget them)."
