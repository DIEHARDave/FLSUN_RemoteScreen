#!/bin/bash
# Removes FLSUN_RemoteScreen.

set -e

CONFIG_DIR="${HOME}/printer_data/config"
MOONRAKER_CONF="${CONFIG_DIR}/moonraker.conf"
ASVC="${HOME}/printer_data/moonraker.asvc"
SERVICE="flsun-remote-screen"
SNIPPET="/etc/nginx/snippets/flsun-remote-screen.conf"
MOONRAKER_URL="http://127.0.0.1:7125"

msg() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

[ "$(id -u)" -ne 0 ] || { echo "run as the pi user, not root" >&2; exit 1; }

msg "Removing the screen service"
sudo systemctl disable --now "${SERVICE}.service" > /dev/null 2>&1 || true
sudo rm -f "/etc/systemd/system/${SERVICE}.service"
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

msg "Removing the webcam entry, if any"
curl -s -m 5 -X DELETE "${MOONRAKER_URL}/server/webcams/item?name=Screen" > /dev/null 2>&1 || true

msg "Removing the Moonraker settings"
sed -i '/^\[include flsun_remote_screen\.conf\]/d' "$MOONRAKER_CONF"
rm -f "${CONFIG_DIR}/flsun_remote_screen.conf" "${CONFIG_DIR}/flsun_remote_screen.env"
if [ -f "$ASVC" ]; then
    sed -i "/^${SERVICE}\$/d" "$ASVC"
fi

# The flsun-timesync boot service from install.sh is left in place on
# purpose: a correct clock helps everything else on the printer too, and
# FLSUN_Timelapse uses the same service.

msg "Restarting Moonraker"
sudo systemctl restart moonraker

msg "Done. Delete ~/FLSUN_RemoteScreen to finish."
