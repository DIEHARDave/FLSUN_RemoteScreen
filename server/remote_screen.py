#!/usr/bin/env python3
# FLSUN_RemoteScreen - view and control the FLSun T1 Pro touchscreen from a
# browser. Reads /dev/fb0, serves it as PNG, and turns clicks into touch
# events on the touch panel's input device.
#
# Based on fb-http from the Snapmaker U1 Extended Firmware screen-apps
# (GPLv3). Rewritten for the T1 Pro: no Pillow (PNG is encoded with zlib),
# and touches are mapped through the running screen's own calibration.
#
# Copyright (C) 2026 DIEHARDave
# This file may be distributed under the terms of the GNU GPLv3 license.

import argparse
import ctypes
import fcntl
import json
import mmap
import os
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602


class FbVarScreeninfo(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32) for n in (
        "xres", "yres", "xres_virtual", "yres_virtual", "xoffset",
        "yoffset", "bits_per_pixel", "grayscale")] + [
        (n, ctypes.c_uint32 * 3) for n in ("red", "green", "blue", "transp")
    ] + [(n, ctypes.c_uint32) for n in (
        "nonstd", "activate", "height", "width", "accel_flags", "pixclock",
        "left_margin", "right_margin", "upper_margin", "lower_margin",
        "hsync_len", "vsync_len", "sync", "vmode", "rotate", "colorspace")
    ] + [("reserved", ctypes.c_uint32 * 4)]


class FbFixScreeninfo(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_char * 16), ("smem_start", ctypes.c_ulong),
        ("smem_len", ctypes.c_uint32), ("type", ctypes.c_uint32),
        ("type_aux", ctypes.c_uint32), ("visual", ctypes.c_uint32),
        ("xpanstep", ctypes.c_uint16), ("ypanstep", ctypes.c_uint16),
        ("ywrapstep", ctypes.c_uint16), ("line_length", ctypes.c_uint32),
        ("mmio_start", ctypes.c_ulong), ("mmio_len", ctypes.c_uint32),
        ("accel", ctypes.c_uint32), ("capabilities", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16 * 2),
    ]


EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0x00
BTN_TOUCH = 0x14a
ABS_MT_SLOT = 0x2f
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39

HELIX_SETTINGS = "/home/pi/helixscreen/config/settings.json"
POINTERCAL = "/etc/pointercal"


def log(msg):
    print(msg, flush=True)


def png_chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))


class Framebuffer:
    """Captures the visible framebuffer page and encodes it as PNG.

    Captures are shared between clients: a frame is re-read at most every
    min_interval seconds and re-encoded only when its pixels change."""

    def __init__(self, device, min_interval=0.05, level=1):
        self.fd = os.open(device, os.O_RDONLY)
        vinfo = FbVarScreeninfo()
        fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, vinfo)
        finfo = FbFixScreeninfo()
        fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO, finfo)
        self.width, self.height = vinfo.xres, vinfo.yres
        self.bpp = vinfo.bits_per_pixel
        self.stride = finfo.line_length
        if self.bpp not in (24, 32):
            raise RuntimeError("unsupported framebuffer depth %d bpp" % self.bpp)
        self.mm = mmap.mmap(self.fd, self.stride * vinfo.yres_virtual,
                            mmap.MAP_SHARED, mmap.PROT_READ)
        self.min_interval = min_interval
        self.level = level
        self.lock = threading.Lock()
        self.etag = None
        self.png = None
        self.captured = 0.0
        log("Framebuffer %s: %dx%d %dbpp stride %d" % (
            device, self.width, self.height, self.bpp, self.stride))

    def _read(self):
        vinfo = FbVarScreeninfo()
        fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, vinfo)
        start = vinfo.yoffset * self.stride
        return self.mm[start:start + self.stride * self.height]

    def _encode(self, raw):
        w, h, px = self.width, self.height, self.bpp // 8
        row = w * px
        if self.stride != row:
            raw = b"".join(raw[y * self.stride:y * self.stride + row]
                           for y in range(h))
        # The panel stores pixels as B, G, R(, X); PNG wants R, G, B.
        rgb = bytearray(w * h * 3)
        rgb[0::3] = raw[2::px]
        rgb[1::3] = raw[1::px]
        rgb[2::3] = raw[0::px]
        line = w * 3
        mv = memoryview(rgb)
        scan = b"".join(b"\x00" + mv[y * line:(y + 1) * line]
                        for y in range(h))
        return (b"\x89PNG\r\n\x1a\n"
                + png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + png_chunk(b"IDAT", zlib.compress(scan, self.level))
                + png_chunk(b"IEND", b""))

    def capture(self):
        """Returns (etag, png) for the current screen contents."""
        with self.lock:
            now = time.monotonic()
            if self.png is not None and now - self.captured < self.min_interval:
                return self.etag, self.png
            raw = self._read()
            etag = "%08x" % (zlib.adler32(raw) & 0xffffffff)
            if etag != self.etag:
                self.png = self._encode(raw)
                self.etag = etag
            self.captured = now
            return self.etag, self.png


class TouchMapper:
    """Maps screen pixels to raw touch-panel coordinates.

    The panel's raw axes are swapped and flipped relative to the screen, and
    each screen corrects for that differently: HelixScreen with the
    input.touch_range block in its settings.json, FLSun's stock Qt screen
    with tslib's /etc/pointercal. Whichever screen is running decides which
    calibration is inverted, so touches land where the user clicked."""

    def __init__(self, width, height, mode="auto"):
        self.width, self.height = width, height
        self.mode = mode
        self._cache = None
        self._cache_key = None
        self.source = "none"

    @staticmethod
    def _running(comm):
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/comm" % pid) as f:
                    if f.read().strip() == comm:
                        return True
            except OSError:
                pass
        return False

    def _pick_source(self):
        if self.mode != "auto":
            return self.mode
        if self._running("helix-screen"):
            return "helixscreen"
        if self._running("TUI"):
            return "tslib"
        return "helixscreen" if os.path.exists(HELIX_SETTINGS) else "tslib"

    @staticmethod
    def _mtime(path):
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None

    def _helix(self):
        with open(HELIX_SETTINGS) as f:
            tr = json.load(f).get("input", {}).get("touch_range", {})
        if not tr.get("valid"):
            return None
        if tr.get("rotation", 0):
            log("HelixScreen touch_range rotation %s is not supported"
                % tr.get("rotation"))
        swap = bool(tr.get("swap_axes"))
        min_x, max_x = tr["min_x"], tr["max_x"]
        min_y, max_y = tr["min_y"], tr["max_y"]
        w, h = self.width, self.height

        def to_raw(sx, sy):
            # lv_evdev: screen = scale(swap ? raw.yx : raw.xy, min..max -> 0..res-1)
            ax = min_x + sx * (max_x - min_x) / max(w - 1, 1)
            ay = min_y + sy * (max_y - min_y) / max(h - 1, 1)
            return (ay, ax) if swap else (ax, ay)
        return to_raw

    def _tslib(self):
        with open(POINTERCAL) as f:
            v = [int(n) for n in f.read().split()]
        a0, a1, a2, a3, a4, a5, a6 = v[:7]
        # Calibration may have been made at another resolution.
        kx = self.width / v[7] if len(v) > 8 and v[7] else 1.0
        ky = self.height / v[8] if len(v) > 8 and v[8] else 1.0
        # tslib: sx = (a2 + a0*x + a1*y)/a6, sy = (a5 + a3*x + a4*y)/a6
        det = a0 * a4 - a1 * a3
        if det == 0:
            return None

        def to_raw(sx, sy):
            u = sx / kx * a6 - a2
            v_ = sy / ky * a6 - a5
            return ((a4 * u - a1 * v_) / det, (a0 * v_ - a3 * u) / det)
        return to_raw

    def mapper(self):
        source = self._pick_source()
        path = HELIX_SETTINGS if source == "helixscreen" else POINTERCAL
        key = (source, self._mtime(path))
        if key != self._cache_key:
            fn = None
            try:
                fn = self._helix() if source == "helixscreen" else self._tslib()
            except (OSError, ValueError, KeyError, IndexError) as e:
                log("Cannot read %s calibration from %s: %s" % (source, path, e))
            if fn is None:
                source = "linear"
            self._cache, self._cache_key = fn, key
            if source != self.source:
                log("Touch mapping: %s" % source)
            self.source = source
        return self._cache

    def to_raw(self, x, y):
        fn = self.mapper()
        if fn is None:
            return int(x), int(y)
        rx, ry = fn(x, y)
        return int(round(rx)), int(round(ry))


class TouchInput:
    """Injects multitouch (protocol B) events into the touch panel's device."""

    RELEASE_AFTER = 4.0  # a press with no events for this long is released

    def __init__(self, device, mapper):
        self.device = device
        self.mapper = mapper
        self.fd = None
        self.lock = threading.Lock()
        self.tracking_id = int(time.time()) & 0xfff
        self.pressed = False
        self.last_event = 0.0
        try:
            self.fd = os.open(device, os.O_WRONLY)
            log("Touch device %s" % device)
        except OSError as e:
            log("Cannot open touch device %s: %s (view only)" % (device, e))
        threading.Thread(target=self._watchdog, daemon=True).start()

    @staticmethod
    def _event(ev_type, code, value):
        t = time.time()
        return struct.pack("llHHi", int(t), int((t % 1) * 1e6),
                           ev_type, code, value)

    def _write(self, events):
        if self.fd is None:
            raise OSError("touch device not available")
        events.append((EV_SYN, SYN_REPORT, 0))
        os.write(self.fd, b"".join(self._event(*e) for e in events))
        self.last_event = time.monotonic()

    def _position(self, x, y):
        rx, ry = self.mapper.to_raw(x, y)
        return [(EV_ABS, ABS_MT_POSITION_X, rx), (EV_ABS, ABS_MT_POSITION_Y, ry)]

    def down(self, x, y):
        with self.lock:
            if self.pressed:
                self._release()
            # The kernel drops a TRACKING_ID equal to the previous one.
            self.tracking_id = (self.tracking_id + 1) & 0xffff
            self._write([(EV_ABS, ABS_MT_SLOT, 0),
                         (EV_ABS, ABS_MT_TRACKING_ID, self.tracking_id)]
                        + self._position(x, y)
                        + [(EV_KEY, BTN_TOUCH, 1)])
            self.pressed = True

    def move(self, x, y):
        with self.lock:
            if self.pressed:
                self._write([(EV_ABS, ABS_MT_SLOT, 0)] + self._position(x, y))

    def _release(self):
        self._write([(EV_ABS, ABS_MT_SLOT, 0),
                     (EV_ABS, ABS_MT_TRACKING_ID, -1),
                     (EV_KEY, BTN_TOUCH, 0)])
        self.pressed = False

    def up(self):
        with self.lock:
            if self.pressed:
                self._release()

    def tap(self, x, y):
        self.down(x, y)
        time.sleep(0.05)
        self.up()

    def _watchdog(self):
        # A browser that disconnects mid-press would leave the finger down.
        while True:
            time.sleep(1.0)
            with self.lock:
                if (self.pressed and time.monotonic() - self.last_event
                        > self.RELEASE_AFTER):
                    log("Releasing a stale touch")
                    try:
                        self._release()
                    except OSError:
                        self.pressed = False


class Handler(BaseHTTPRequestHandler):
    fb = None
    touch = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body=b"", ctype="application/json", headers=()):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        if code != 304:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        path = url.path.rstrip("/") or "/"
        if path == "/snapshot":
            self.snapshot(q)
        elif path == "/stream":
            self.stream(q)
        elif path == "/info":
            self.touch.mapper.mapper()
            self._json(200, {
                "width": self.fb.width, "height": self.fb.height,
                "touch": self.touch.fd is not None,
                "touch_mapping": self.touch.mapper.source})
        else:
            self._json(404, {"error": "not found"})

    do_HEAD = do_GET

    def do_POST(self):
        url = urlparse(self.path)
        if url.path.rstrip("/") != "/touch":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        q = parse_qs(url.query)
        action = q.get("a", ["tap"])[0]
        try:
            x = min(max(int(float(q.get("x", ["0"])[0])), 0), self.fb.width - 1)
            y = min(max(int(float(q.get("y", ["0"])[0])), 0), self.fb.height - 1)
            if action == "down":
                self.touch.down(x, y)
            elif action == "move":
                self.touch.move(x, y)
            elif action == "up":
                self.touch.up()
            elif action == "tap":
                self.touch.tap(x, y)
            else:
                self._json(400, {"error": "unknown action"})
                return
        except (OSError, ValueError) as e:
            self._json(503 if isinstance(e, OSError) else 400, {"error": str(e)})
            return
        self._json(200, {"status": "ok", "a": action, "x": x, "y": y})

    def snapshot(self, q):
        """GET /snapshot[?wait=seconds]. With If-None-Match and wait, holds
        the request until the screen changes (long poll), else answers 304."""
        client = self.headers.get("If-None-Match", "").strip('"')
        try:
            wait = min(float(q.get("wait", ["0"])[0]), 25.0)
        except ValueError:
            wait = 0.0
        deadline = time.monotonic() + wait
        while True:
            etag, png = self.fb.capture()
            if etag != client or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        headers = [("ETag", '"%s"' % etag)]
        if etag == client:
            self._send(304, headers=headers)
        else:
            self._send(200, png, "image/png", headers)

    def stream(self, q):
        """GET /stream[?fps=n]: multipart PNG stream, a frame per change."""
        try:
            interval = 1.0 / min(max(float(q.get("fps", ["5"])[0]), 0.2), 20.0)
        except ValueError:
            interval = 0.2
        boundary = "frame"
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=" + boundary)
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        last, sent = None, 0.0
        try:
            while True:
                etag, png = self.fb.capture()
                now = time.monotonic()
                # Resend now and then so viewers that dropped a part recover.
                if etag != last or now - sent > 5.0:
                    self.wfile.write((
                        "--%s\r\nContent-Type: image/png\r\n"
                        "Content-Length: %d\r\n\r\n" % (boundary, len(png))
                    ).encode() + png + b"\r\n")
                    self.wfile.flush()
                    last, sent = etag, now
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    p = argparse.ArgumentParser(description="FLSun T1 Pro remote screen")
    p.add_argument("--bind", default="127.0.0.1",
                   help="address to listen on (nginx proxies to it)")
    p.add_argument("--port", type=int, default=8093)
    p.add_argument("--fb", default="/dev/fb0")
    p.add_argument("--touch", default="/dev/input/event1")
    p.add_argument("--mapping", default="auto",
                   choices=("auto", "helixscreen", "tslib", "linear"),
                   help="touch calibration to invert (auto follows the "
                        "running screen)")
    p.add_argument("--compression", type=int, default=1,
                   help="PNG zlib level 0-9; higher is smaller but slower")
    args = p.parse_args()

    fb = Framebuffer(args.fb, level=args.compression)
    mapper = TouchMapper(fb.width, fb.height, args.mapping)
    Handler.fb = fb
    Handler.touch = TouchInput(args.touch, mapper)
    mapper.mapper()

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.daemon_threads = True
    log("Listening on http://%s:%d" % (args.bind, args.port))
    server.serve_forever()


if __name__ == "__main__":
    main()
