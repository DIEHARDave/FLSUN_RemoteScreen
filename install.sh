#!/bin/bash
# FLSUN_RemoteScreen installer for the FLSun T1 Pro.
# Run as the pi user from the cloned repo: ~/FLSUN_RemoteScreen/install.sh
# Options:
#   --no-webcam   don't add the "Screen" webcam to Mainsail's and Fluidd's
#                 dashboard
# Safe to re-run, e.g. after a FLSun firmware update.

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${HOME}/printer_data/config"
MOONRAKER_CONF="${CONFIG_DIR}/moonraker.conf"
ASVC="${HOME}/printer_data/moonraker.asvc"
SERVICE="flsun-remote-screen"
SNIPPET="/etc/nginx/snippets/flsun-remote-screen.conf"

msg() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

ADD_WEBCAM=1
for arg in "$@"; do
    case "$arg" in
        --no-webcam) ADD_WEBCAM=0 ;;
        *) die "unknown option: $arg" ;;
    esac
done

[ "$(id -u)" -ne 0 ] || die "run as the pi user, not root (sudo is used where needed)"
# The service and nginx config point at this path.
[ "$REPO_DIR" = "${HOME}/FLSUN_RemoteScreen" ] \
    || die "clone the repo to ${HOME}/FLSUN_RemoteScreen (it is at $REPO_DIR)"
command -v python3 > /dev/null || die "python3 not found"
[ -e /dev/fb0 ] || die "/dev/fb0 not found"
[ -f "$MOONRAKER_CONF" ] || die "$MOONRAKER_CONF not found"
[ -d /etc/nginx/sites-enabled ] || die "nginx not found"

# Clock, certificates and git https support, shared with FLSUN_Timelapse;
# see prepare.sh.
"${REPO_DIR}/prepare.sh"

msg "Installing the boot service that sets the clock"
sudo install -m 755 "${REPO_DIR}/system/flsun-timesync" /usr/local/bin/flsun-timesync
sudo install -m 644 "${REPO_DIR}/system/flsun-timesync.service" /etc/systemd/system/flsun-timesync.service
sudo systemctl daemon-reload
sudo systemctl enable flsun-timesync.service > /dev/null 2>&1

msg "Installing the screen service"
chmod 755 "${REPO_DIR}/server/remote_screen.py" "${REPO_DIR}/server/screen_webcam.py"
if [ ! -f "${CONFIG_DIR}/flsun_remote_screen.env" ]; then
    cp "${REPO_DIR}/config/flsun_remote_screen.env" "${CONFIG_DIR}/flsun_remote_screen.env"
fi
sudo install -m 644 "${REPO_DIR}/system/${SERVICE}.service" "/etc/systemd/system/${SERVICE}.service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE}.service" > /dev/null 2>&1
sudo systemctl restart "${SERVICE}.service"

msg "Adding /screen/ to the web server"
sudo mkdir -p /etc/nginx/snippets
sudo install -m 644 "${REPO_DIR}/nginx/flsun-remote-screen.conf" "$SNIPPET"
backups=()
for link in /etc/nginx/sites-enabled/*; do
    site=$(readlink -f "$link")
    [ -f "$site" ] || continue
    grep -q 'server_name' "$site" || continue
    grep -q "include ${SNIPPET};" "$site" && continue
    sudo cp "$site" "${site}.flsun_remote_screen.bak"
    backups+=("$site")
    # Mainsail's site has a single server block; add the include inside it.
    sudo sed -i "0,/^\([[:space:]]*\)server_name .*;/s||&\n\1include ${SNIPPET};|" "$site"
done
if ! sudo nginx -t > /dev/null 2>&1; then
    for site in "${backups[@]}"; do
        sudo mv "${site}.flsun_remote_screen.bak" "$site"
    done
    sudo nginx -t || true
    die "nginx rejected the configuration; your sites were restored"
fi
sudo systemctl reload nginx

msg "Adding the Moonraker update manager entry"
if [ ! -f "${CONFIG_DIR}/flsun_remote_screen.conf" ]; then
    cp "${REPO_DIR}/config/flsun_remote_screen.conf" "${CONFIG_DIR}/flsun_remote_screen.conf"
fi
if ! grep -q '^\[include flsun_remote_screen\.conf\]' "$MOONRAKER_CONF"; then
    cp "$MOONRAKER_CONF" "${MOONRAKER_CONF}.flsun_remote_screen.bak"
    printf '\n[include flsun_remote_screen.conf]\n' >> "$MOONRAKER_CONF"
fi
# Let Moonraker restart the service after an update.
if [ -f "$ASVC" ] && ! grep -qx "$SERVICE" "$ASVC"; then
    # FLSun's file has no final newline.
    [ -z "$(tail -c 1 "$ASVC")" ] || echo >> "$ASVC"
    echo "$SERVICE" >> "$ASVC"
fi

msg "Restarting Moonraker"
sudo systemctl restart moonraker

if [ "$ADD_WEBCAM" = 1 ]; then
    # A second webcam, listed after the camera so timelapses and HelixScreen
    # keep using the camera; see server/screen_webcam.py.
    msg "Adding the Screen webcam to the dashboard"
    python3 "${REPO_DIR}/server/screen_webcam.py" add || warn "could not add the Screen webcam"
fi

sleep 1
if ! systemctl is-active --quiet "$SERVICE"; then
    sudo journalctl -u "$SERVICE" -n 20 --no-pager || true
    die "the ${SERVICE} service is not running; see the log above"
fi
ip=$(hostname -I 2>/dev/null | awk '{print $1}')
msg "Done. Open http://${ip:-<printer-ip>}/screen/ in a browser."
