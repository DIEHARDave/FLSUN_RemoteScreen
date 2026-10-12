# FLSUN_RemoteScreen

View and control the **FLSun T1 Pro** touchscreen from any web browser on a phone, tablet or computer. This is the same kind of remote screen that the Snapmaker U1 Extended Firmware and Zmod for Flashforge printers offer.

Open `http://<printer-ip>/screen/` to see the printer's screen live. Click or tap to press its buttons, and drag to swipe or scroll.

## What it does

- Mirrors the 480×272 screen and refreshes as soon as anything on it changes.
- Turns clicks, taps and drags into real touches on the printer's screen.
- Works with HelixScreen and with FLSun's stock screen. Each one calibrates the touch panel differently, so the plugin follows whichever screen is running.
- Uses the same login as Mainsail and Fluidd. If Moonraker asks for a login, the screen asks for it too.
- Can be installed as an app from the browser's "Add to Home Screen".
- Updates from Mainsail's and Fluidd's update manager.

It needs nothing beyond what's already on the printer, and it sends a new image only when the screen changes, so it uses very little CPU.

## Install

SSH into the printer as `pi` (the default password is `1`).

If git can't reach GitHub, the system needs FLSun's image fixes first. They are the same ones [FLSUN_Timelapse](https://github.com/DIEHARDave/FLSUN_Timelapse) makes; skip this step if you already installed that plugin:

```sh
sudo ntpdate -u -b time.google.com
curl -fsSL --cacert /etc/ssl/certs/ca-certificates.crt https://raw.githubusercontent.com/DIEHARDave/FLSUN_Timelapse/main/prepare.sh | bash
```

Then download and install:

```sh
cd ~
git clone https://github.com/DIEHARDave/FLSUN_RemoteScreen.git
~/FLSUN_RemoteScreen/install.sh
```

The installer restarts Moonraker but not Klipper, so it's safe to run during a print. Run it again after a FLSun firmware update that resets the web server.

## Use

Open `http://<printer-ip>/screen/` in a browser. The dot in the top-right corner is green while the page is connected; the button next to it switches to full screen.

If the printer's screen has gone dark to save power, the page shows black. Tap anywhere to wake it, just as you would on the printer.

### Show it in Mainsail or Fluidd (optional)

```sh
~/FLSUN_RemoteScreen/install.sh --webcam
```

This adds a webcam named **Screen** that shows the printer's screen in the dashboard's webcam panel. It's view only: the Mainsail version on the T1 Pro (2.12) can't embed pages, so use `/screen/` to touch.

Before using this with FLSUN_Timelapse, add `camera: Webcam` under `[timelapse]` in `~/printer_data/config/flsun_timelapse.conf`. Without that line, the timelapse records the first webcam Moonraker lists, and that could be the screen. HelixScreen's camera view can pick the wrong one the same way. To remove the entry, delete the Screen webcam in Mainsail's or Fluidd's webcam settings.

## Settings

Options are in `~/printer_data/config/flsun_remote_screen.env`. The installer creates this file once and never overwrites it. After editing it, run `sudo systemctl restart flsun-remote-screen`.

| Option | Default | Meaning |
|---|---|---|
| `--mapping` | `auto` | Which touch calibration to follow: `helixscreen` (its `input.touch_range`), `tslib` (FLSun's `/etc/pointercal`) or `linear`. `auto` picks the one belonging to the screen that's running. |
| `--compression` | `1` | PNG compression level, 0–9. Higher levels send smaller images but use more CPU. |
| `--touch` | `/dev/input/event1` | Touch panel device |
| `--fb` | `/dev/fb0` | Framebuffer device |
| `--port` | `8093` | Local port that nginx forwards to |

If you recalibrate HelixScreen's touch, the remote screen picks up the new calibration automatically.

## What the installer changes

| File | Change |
|---|---|
| `/etc/systemd/system/flsun-remote-screen.service` | New service. It runs `server/remote_screen.py` as `pi`, with the `video` and `input` groups so it can read the screen and send touches |
| `/etc/nginx/snippets/flsun-remote-screen.conf` | New file: the `/screen/` web paths |
| `/etc/nginx/sites-available/mainsail` | Adds one `include` line for the snippet (a backup is saved first) |
| `~/printer_data/config/flsun_remote_screen.conf` | New file: update manager entry |
| `~/printer_data/config/flsun_remote_screen.env` | New file: options |
| `~/printer_data/config/moonraker.conf` | Adds `[include flsun_remote_screen.conf]` (a backup is saved first) |
| `~/printer_data/moonraker.asvc` | Adds `flsun-remote-screen`, so the update manager can restart it |

## How it works

`server/remote_screen.py` uses only the Python standard library that ships on the printer (3.7, with no Pillow or pip):

- **Screen:** it reads `/dev/fb0` and encodes a PNG with zlib, which takes about 25 ms on the T1 Pro. A checksum of the pixels serves as the ETag. The page long-polls `/screen/snapshot?wait=10`, and the server holds that request until the screen changes, so updates arrive quickly without repeated requests. `/screen/stream` serves the same images as a multipart stream.
- **Touch:** it writes multitouch (protocol B) events into the touch panel's own input device, so the screen software receives them as real touches. The panel's raw axes are swapped and mirrored relative to the display, so each click is converted with the inverse of the running screen's calibration. A press with no events for 4 seconds is released automatically, in case the browser disconnects mid-touch.
- **Access:** the server listens only on 127.0.0.1. nginx serves the page and forwards the image and touch requests after asking Moonraker (`/access/user`) whether the client is allowed.

## Uninstall

```sh
~/FLSUN_RemoteScreen/uninstall.sh
rm -rf ~/FLSUN_RemoteScreen
```

## Credits

- [Snapmaker U1 Extended Firmware](https://github.com/paxx12/SnapmakerU1-Extended-Firmware) and the `fb-http` app from [paxx12/screen-apps](https://github.com/paxx12/screen-apps), which this design follows
- [Zmod](https://github.com/ghzserg/zmod) for Flashforge printers, for the idea

Licensed under GPLv3.
