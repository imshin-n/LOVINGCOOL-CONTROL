#!/usr/bin/env bash
# lovingcool-control :: packaging/linux/uninstall_service.sh
# =====================================================
# Stops and removes the lovingcool-control systemd --user service. Leaves the
# installed binary at ~/.local/share/lovingcool-control/lovingcool-control in place —
# delete that directory yourself if you want it fully gone.

set -euo pipefail

UNIT_DIR="$HOME/.config/systemd/user"

systemctl --user disable --now lovingcool-control.service 2>/dev/null || true
rm -f "$UNIT_DIR/lovingcool-control.service"
systemctl --user daemon-reload

echo "Service stopped and removed."
echo "Binary left at ~/.local/share/lovingcool-control/lovingcool-control — remove manually if desired:"
echo "  rm -rf ~/.local/share/lovingcool-control"
