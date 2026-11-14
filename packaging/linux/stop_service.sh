#!/usr/bin/env bash
# lovingcool-control :: packaging/linux/stop_service.sh
# ================================================
# Stops the running instance WITHOUT triggering the service's
# Restart=always auto-restart loop. Closing the app's window alone does
# NOT do this — systemd would just relaunch it a few seconds later.

set -euo pipefail
systemctl --user stop lovingcool-control.service
echo "Stopped. It will not come back until: systemctl --user start lovingcool-control"
