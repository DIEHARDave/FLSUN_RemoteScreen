#!/bin/bash
# Removes FLSUN_RemoteScreen.

set -e

CONFIG_DIR="${HOME}/printer_data/config"
MOONRAKER_CONF="${CONFIG_DIR}/moonraker.conf"
ASVC="${HOME}/printer_data/moonraker.asvc"
SERVICE="flsun_remote_screen"
# The service's name before v0.2.0.
OLD_SERVICE="flsun-remote-screen"
SNIPPET="/etc/nginx/snippets/flsun-remote-screen.conf"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

msg() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

[ "$(id -u)" -ne 0 ] || { echo "run as the pi user, not root" >&2; exit 1; }

msg "Removing the screen service"
for svc in "$SERVICE" "$OLD_SERVICE"; do
    sudo systemctl disable --now "${svc}.service" > /dev/null 2>&1 || true
    sudo rm -f "/etc/systemd/system/${svc}.service"
done
sudo systemctl daemon-reload

msg "Removing /screen/ from the web server"
for link in /etc/nginx/sites-enabled/*; do
    site=$(readlink -f "$link")
    [ -f "$site" ] || continue
    sudo sed -i "\|include ${SNIPPET};|d" "$site"
    sudo rm -f "${site}.flsun_remote_screen.bak"
done
sudo rm -f "$SNIPPET"
sudo nginx -t > /dev/null 2>&1 && sudo systemctl reload nginx

msg "Removing the Screen webcam, if any"
python3 "${REPO_DIR}/server/screen_webcam.py" remove || true

msg "Removing the Moonraker settings"
sed -i '/^\[include flsun_remote_screen\.conf\]/d' "$MOONRAKER_CONF"
rm -f "${CONFIG_DIR}/flsun_remote_screen.conf" "${CONFIG_DIR}/flsun_remote_screen.env"
if [ -f "$ASVC" ]; then
    sed -i -e "/^${SERVICE}\$/d" -e "/^${OLD_SERVICE}\$/d" "$ASVC"
fi

# The flsun-timesync boot service from install.sh is left in place on
# purpose: a correct clock helps everything else on the printer too, and
# FLSUN_Timelapse uses the same service.

msg "Restarting Moonraker"
sudo systemctl restart moonraker

msg "Done. Delete ~/FLSUN_RemoteScreen to finish."
