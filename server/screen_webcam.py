#!/usr/bin/env python3
# Adds or removes the "Screen" webcam that shows the printer's screen in
# Mainsail's and Fluidd's webcam panel. Used by install.sh and uninstall.sh.
#
#   screen_webcam.py add | remove
#
# FLSUN_Timelapse records the first webcam Moonraker lists, and HelixScreen
# shows the first MJPEG one, so Screen must never come before the camera.
# This Moonraker lists webcams from its database sorted by their random uid,
# so Screen is re-created until its uid sorts after every other webcam's.
#
# Copyright (C) 2026 DIEHARDave
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MOONRAKER = "http://127.0.0.1:7125"
NAME = "Screen"
SETTINGS = {
    "service": "mjpegstreamer-adaptive",
    "stream_url": "/screen/stream",
    "snapshot_url": "/screen/snapshot",
    "aspect_ratio": "16:9",
    "target_fps": "10",
    "target_fps_idle": "2",
}
MAX_TRIES = 50


def call(method, path, params=None):
    url = MOONRAKER + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)["result"]


def wait_for_moonraker():
    for _ in range(60):
        try:
            call("GET", "/server/info")
            return
        except (OSError, ValueError):
            time.sleep(1)
    sys.exit("Moonraker is not answering at " + MOONRAKER)


def webcams():
    return call("GET", "/server/webcams/list")["webcams"]


def in_place(cams):
    """True when Screen exists with our URLs and sorts after the others."""
    screen = next((c for c in cams if c["name"] == NAME), None)
    if screen is None:
        return False
    if any(str(screen.get(k)) != SETTINGS[k]
           for k in ("service", "stream_url", "snapshot_url")):
        return False
    # Webcams from moonraker.conf are always listed before database ones.
    others = [c["uid"] for c in cams
              if c["name"] != NAME and c.get("source") != "config"]
    return all(screen["uid"] > uid for uid in others)


def add():
    wait_for_moonraker()
    for _ in range(MAX_TRIES):
        cams = webcams()
        if in_place(cams):
            print("Webcam %s is set up, after %s" % (NAME, ", ".join(
                c["name"] for c in cams if c["name"] != NAME) or "no other webcams"))
            return
        if any(c["name"] == NAME for c in cams):
            call("DELETE", "/server/webcams/item", {"name": NAME})
        call("POST", "/server/webcams/item", dict(SETTINGS, name=NAME))
    sys.exit("could not place webcam %s after the camera; delete it in "
             "Mainsail's webcam settings if timelapses record the screen" % NAME)


def remove():
    wait_for_moonraker()
    if any(c["name"] == NAME for c in webcams()):
        call("DELETE", "/server/webcams/item", {"name": NAME})
        print("Removed webcam %s" % NAME)


if __name__ == "__main__":
    actions = {"add": add, "remove": remove}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        sys.exit("usage: screen_webcam.py add|remove")
    try:
        actions[sys.argv[1]]()
    except urllib.error.HTTPError as e:
        sys.exit("Moonraker refused the request: %s" % e)
