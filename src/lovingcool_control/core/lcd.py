"""
lovingcool-control :: core/lcd.py
=================================
Image, GIF, video and text -> LCD frame pipeline.

Protocol (confirmed on 33C3:7788 "txm818-7inch", firmware "Ver1.0", from
the vendor's Electron app source; see README). The LCD channel takes
JPEG frames, not raw pixels:

  1. Once per live session: command 0x11 ("commit" / start live).
  2. Per frame: resize to the panel's native raster, JPEG-encode at the
     highest quality that fits a size cap (~50 KB by default), and write
     the JPEG bytes straight to the serial port. Firmware > 2.8 wraps the
     JPEG as [4-byte LE length][jpeg][2-byte LE checksum]; Ver1.0 doesn't.

One LCDController exists per device (LCDController.for_device), and it
runs at most one playback (GIF, video or live scene) at a time: starting
a new one stops the old one, so two streams never fight over the port.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
import weakref
from collections.abc import Callable, Iterable, Iterator
from importlib.util import find_spec
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageSequence

from .device import AIODevice, DeviceError

# OpenCV (and the numpy it pulls in) costs ~40 MB of RAM, and only video
# needs it, so it's imported on first use rather than at startup.
CV2_AVAILABLE = find_spec("cv2") is not None

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".webm")

Frame = tuple[Image.Image, int]   # (image, duration_ms)


# ──────────────────────────────────────────────────────────────
#  Encoding
# ──────────────────────────────────────────────────────────────

def _rgb_array(img: Image.Image):
    import numpy as np  # only the (legacy) raw-pixel formats need it

    return np.asarray(img.convert("RGB"), dtype=np.uint16)


def image_to_rgb565(img: Image.Image) -> bytes:
    """RGB565, little-endian."""
    a = _rgb_array(img)
    v = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    return v.astype("<u2").tobytes()


def image_to_bgr565(img: Image.Image) -> bytes:
    """BGR565, little-endian (red and blue swapped)."""
    a = _rgb_array(img)
    v = ((a[..., 2] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 0] >> 3)
    return v.astype("<u2").tobytes()


def image_to_rgb888(img: Image.Image) -> bytes:
    return img.convert("RGB").tobytes()


FORMAT_ENCODERS = {
    "RGB565": image_to_rgb565,
    "BGR565": image_to_bgr565,
    "RGB888": image_to_rgb888,
}


def fit_jpeg(img: Image.Image, max_size_kb: float, hint: int | None = None) -> tuple[bytes, int]:
    """
    JPEG-encode at the highest quality (1-100) whose output fits max_size_kb.
    Returns (data, quality). If even quality 1 is too big, returns quality 1
    anyway, as the vendor app does.

    Binary search: about 7 encodes worst case, instead of the up-to-50 of
    stepping down by 2 from 100. Passing the previous frame's quality as
    `hint` usually settles it in 2 encodes, since consecutive frames of a
    video or scene compress alike.
    """
    img = img.convert("RGB")
    limit = max_size_kb * 1024
    cache: dict[int, bytes] = {}

    def enc(q: int) -> bytes:
        if q not in cache:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=q)
            cache[q] = buf.getvalue()
        return cache[q]

    def fits(q: int) -> bool:
        return len(enc(q)) <= limit

    lo, hi = 1, 100
    if hint is not None and 1 <= hint <= 100:
        if fits(hint):
            if hint == 100 or not fits(hint + 1):
                return enc(hint), hint
            lo = hint + 1
        else:
            if hint > 1 and fits(hint - 1):
                return enc(hint - 1), hint - 1
            hi = max(1, hint - 2)
    # Invariant: the answer is in [lo, hi] (or 1 if nothing fits).
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid - 1
    return enc(lo), lo


def encode_jpeg_capped(img: Image.Image, max_size_kb: float) -> bytes:
    """JPEG at the highest quality that fits max_size_kb (see fit_jpeg)."""
    return fit_jpeg(img, max_size_kb)[0]


# ──────────────────────────────────────────────────────────────
#  Sources: images, GIFs, video, text
# ──────────────────────────────────────────────────────────────

def load_image(path: str | Path) -> Image.Image:
    return Image.open(path)


def load_gif_frames(path: str | Path) -> list[Frame]:
    """All frames of a GIF as (frame, duration_ms)."""
    gif = Image.open(path)
    return [(frame.copy(), frame.info.get("duration", 100)) for frame in ImageSequence.Iterator(gif)]


def _require_cv2(purpose: str):
    """Import OpenCV on first use (see CV2_AVAILABLE above)."""
    if not CV2_AVAILABLE:
        raise RuntimeError(f"{purpose} needs opencv-python-headless: pip install opencv-python-headless")
    import cv2

    return cv2


def iter_video_frames(path: str | Path, max_side: int | None = None, loop: bool = False) -> Iterator[Frame]:
    """
    Decode a video lazily, one frame at a time, as (RGB image, duration_ms).

    Frames are downscaled while decoding so the longer side is at most
    max_side, which keeps memory flat no matter how long or large the
    video is. With loop=True the file is reopened at the end, forever.
    """
    cv2 = _require_cv2("Video playback")
    while True:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video: {path}")
        produced = False
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
            duration_ms = max(1, round(1000 / fps))
            while True:
                ok, frame_bgr = cap.read()
                if not ok:
                    break
                if max_side:
                    h, w = frame_bgr.shape[:2]
                    scale = max_side / max(w, h)
                    if scale < 1:
                        frame_bgr = cv2.resize(frame_bgr, (max(1, round(w * scale)), max(1, round(h * scale))),
                                               interpolation=cv2.INTER_AREA)
                produced = True
                yield Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)), duration_ms
        finally:
            cap.release()
        if not produced:
            raise RuntimeError(f"No frames decoded from video: {path}")
        if not loop:
            return


def load_video_thumbnail(path: str | Path) -> Image.Image:
    """Just the first frame of a video, for a preview."""
    _require_cv2("Video preview")
    return next(iter_video_frames(path))[0]


def cycle_frames(frames: list[Frame], loop: bool) -> Iterator[Frame]:
    """Yield a finite frame list once, or forever."""
    while True:
        yield from frames
        if not loop or not frames:
            return


def render_text_image(
    text: str,
    width: int,
    height: int,
    font_size: int = 40,
    fg_color: tuple = (255, 255, 255),
    bg_color: tuple = (0, 0, 0),
    font_path: str | None = None,
) -> Image.Image:
    """Render centred text onto a blank image."""
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default(font_size)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (width - (bbox[2] - bbox[0])) // 2 - bbox[0]
    y = (height - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((x, y), text, fill=fg_color, font=font)
    return img


# ──────────────────────────────────────────────────────────────
#  Orientation (landscape/portrait), shared with core/canvas.py
# ──────────────────────────────────────────────────────────────

def logical_canvas_size(device_width: int, device_height: int, orientation: str) -> tuple[int, int]:
    """
    The panel's raster is fixed (device_width x device_height). "Portrait"
    means content is composed on a swapped-aspect canvas and the finished
    frame is rotated 90 degrees into the native buffer at send time (see
    orient_for_device). Editors and previews work at this logical size.
    """
    if orientation == "portrait":
        return device_height, device_width
    return device_width, device_height


def orient_for_device(
    composed: Image.Image,
    device_width: int,
    device_height: int,
    orientation: str,
    fill_color: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """Turn a frame composed at the logical size into one at the native raster size."""
    if orientation == "portrait":
        composed = composed.rotate(90, resample=Image.BICUBIC, expand=True, fillcolor=fill_color)
    if composed.size != (device_width, device_height):
        composed = composed.resize((device_width, device_height), Image.LANCZOS)
    return composed


# ──────────────────────────────────────────────────────────────
#  Controller
# ──────────────────────────────────────────────────────────────

class LCDController:
    """
    Sends frames to one device's LCD and owns its single playback thread.

    Get it with LCDController.for_device(device) so every part of the app
    shares one controller (and so one playback) per device.

    JPEG devices (the confirmed 33C3 family) get one JPEG per frame after
    a one-time commit. Other formats use a legacy chunked raw-pixel path
    that has never been verified on hardware.
    """

    DEFAULT_MAX_JPEG_KB = 50   # vendor default for firmware <= 2.8

    _instances: weakref.WeakKeyDictionary[AIODevice, LCDController] = weakref.WeakKeyDictionary()
    _instances_lock = threading.Lock()

    @classmethod
    def for_device(cls, device: AIODevice) -> LCDController:
        with cls._instances_lock:
            ctl = cls._instances.get(device)
            if ctl is None:
                ctl = cls._instances[device] = cls(device)
            return ctl

    def __init__(self, device: AIODevice):
        self.device = device
        self._color_format = device.spec.lcd_color_format
        self._live_started = False
        self._use_frame_wrapper = False   # set by refresh_info() for firmware > 2.8
        self._max_jpeg_kb = self.DEFAULT_MAX_JPEG_KB
        self._jpeg_quality_hint: int | None = None
        self._info_loaded = False
        # Keep-alive: last frame as sent on the wire, and when anything was last sent.
        self._last_wire: bytes | None = None
        self._last_send = 0.0
        self._keepalive_thread: threading.Thread | None = None
        self._keepalive_stop = threading.Event()
        # Playback (one at a time)
        self._play_thread: threading.Thread | None = None
        self._play_stop = threading.Event()
        self._play_lock = threading.Lock()
        self.last_error: str | None = None

    # ── Device info ───────────────────────────────────────────

    @property
    def size(self) -> tuple[int, int]:
        """Native panel raster (width, height)."""
        return self.device.panel_size

    def refresh_info(self) -> dict:
        """Query the device; sets panel size (via the device) and firmware-dependent framing."""
        info = {}
        if self.device.spec.transport == "serial":
            with self.device.exclusive():
                was_live = self._live_started
                self._end_stream()
                try:
                    info = self.device.get_info()
                finally:
                    if was_live and self._last_wire is not None and self.device.connected:
                        self._send_wire(self._last_wire)
        match = re.search(r"\d+(?:\.\d+)?", str(info.get("version", "")))
        version = float(match.group()) if match else None
        self._use_frame_wrapper = version is not None and version > 2.8
        self._info_loaded = True
        log.info("LCD info: %s (panel %dx%d, frame wrapper %s)", info, *self.size, self._use_frame_wrapper)
        return info

    def ensure_info(self) -> None:
        if not self._info_loaded:
            self.refresh_info()

    # ── Low-level sending ─────────────────────────────────────

    def _build_jpeg_frame(self, jpeg_bytes: bytes) -> bytes:
        if not self._use_frame_wrapper:
            return jpeg_bytes
        body = len(jpeg_bytes).to_bytes(4, "little") + jpeg_bytes
        return body + (sum(body) & 0xFFFF).to_bytes(2, "little")

    def send_raw_jpeg_frame(self, jpeg_bytes: bytes) -> None:
        """Send one already-encoded JPEG (commit first if this live session hasn't)."""
        self._send_wire(self._build_jpeg_frame(jpeg_bytes))

    def _send_wire(self, wire: bytes) -> None:
        with self.device.exclusive():
            if not self._live_started:
                self.device.send_command(self.device.spec.cmd_commit, expect_response=False)
                self._live_started = True
            self.device.send_packet(wire)
            self._last_wire = wire
            self._last_send = time.monotonic()
        self._ensure_keepalive()

    # ── Keep-alive (firmware that blanks without a steady stream) ──

    def _ensure_keepalive(self) -> None:
        if self.device.spec.keepalive_ms <= 0:
            return
        if (self._keepalive_thread and self._keepalive_thread.is_alive()
                and not self._keepalive_stop.is_set()):
            return
        # A fresh event per thread: a previous thread that is still winding down
        # after stop() keeps its own (set) event and can't cancel this one.
        stop = self._keepalive_stop = threading.Event()
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, args=(stop,),
                                                  daemon=True, name="lcd-keepalive")
        self._keepalive_thread.start()

    def _keepalive_loop(self, stop: threading.Event) -> None:
        """Re-send the last frame whenever nothing else has been sent for keepalive_ms."""
        period = self.device.spec.keepalive_ms / 1000.0
        while not stop.wait(period / 4):
            with self.device.exclusive():
                if not self._live_started or self._last_wire is None or not self.device.connected:
                    return
                if time.monotonic() - self._last_send < period:
                    continue
                try:
                    self.device.send_packet(self._last_wire)
                except DeviceError as e:
                    log.warning("Keep-alive stopped: %s", e)
                    return
                self._last_send = time.monotonic()

    def send_raw_frame(self, pixel_data: bytes) -> None:
        """Legacy chunked raw-pixel transmission (unverified; see class docstring)."""
        spec = self.device.spec
        chunk_size = 60
        with self.device.exclusive():
            self.device.send_packet(bytes([spec.cmd_commit, 0x00] + [0x00] * 62))
            for seq, offset in enumerate(range(0, len(pixel_data), chunk_size)):
                header = bytes([spec.cmd_commit, (seq >> 8) & 0xFF, seq & 0xFF, 0x00])
                self.device.send_packet((header + pixel_data[offset:offset + chunk_size]).ljust(64, b"\0"))

    def stop(self) -> None:
        """End the live session (stops the keep-alive); the next frame re-sends the commit."""
        self._keepalive_stop.set()
        with self.device.exclusive():
            self._end_stream()
            self._last_wire = None

    def _end_stream(self) -> None:
        if self._live_started and self._use_frame_wrapper and self.device.connected:
            # The vendor app resets the image stream with a double JPEG EOI.
            self.device.send_packet(bytes([0xFF, 0xD9, 0xFF, 0xD9]))
        self._live_started = False

    # ── Commands while streaming ──────────────────────────────

    def command(self, key: int, payload: bytes = b"", expect_response: bool = True) -> dict | None:
        """
        Send a command-channel frame, safely even mid-stream. Firmware 3.x
        ignores commands while a JPEG stream is open, so the stream is ended
        first and the last frame re-sent afterwards (the panel only blanks
        after ~0.5 s without frames, so this doesn't flicker).
        """
        with self.device.exclusive():
            was_live = self._live_started
            self._end_stream()
            try:
                return self.device.send_command(key, payload, expect_response=expect_response)
            finally:
                if was_live and self._last_wire is not None and self.device.connected:
                    self._send_wire(self._last_wire)

    def set_brightness(self, percent: int) -> None:
        """LCD backlight, 0-100."""
        self.command(self.device.spec.cmd_set_brightness, bytes([max(0, min(100, percent))]),
                     expect_response=False)

    # ── Frames ────────────────────────────────────────────────

    def prepare(self, img: Image.Image, rotation: int = 0, orientation: str = "landscape") -> Image.Image:
        """Fit an image to the logical canvas, apply fine rotation, then orient to the native raster."""
        width, height = self.size
        logical_w, logical_h = logical_canvas_size(width, height, orientation)
        img = img.convert("RGB")
        if img.size != (logical_w, logical_h):
            img = img.resize((logical_w, logical_h), Image.LANCZOS)
        if rotation % 360:
            img = img.rotate(-rotation, resample=Image.BICUBIC, expand=False)
        return orient_for_device(img, width, height, orientation)

    def send_frame(self, native: Image.Image) -> None:
        """Encode and send an image that is already at the native raster size."""
        if self._color_format == "JPEG":
            data, self._jpeg_quality_hint = fit_jpeg(native, self._max_jpeg_kb, self._jpeg_quality_hint)
            self.send_raw_jpeg_frame(data)
        else:
            encoder = FORMAT_ENCODERS.get(self._color_format, image_to_rgb565)
            self.send_raw_frame(encoder(native))

    def send_image(self, img: Image.Image, rotation: int = 0, orientation: str = "landscape") -> None:
        """Resize/orient a PIL image for the panel and send it."""
        self.ensure_info()
        self.send_frame(self.prepare(img, rotation, orientation))

    def send_image_file(self, path: str | Path, rotation: int = 0, orientation: str = "landscape") -> None:
        self.send_image(load_image(path), rotation, orientation)

    def send_text(self, text: str, font_size: int = 40, fg: tuple = (255, 255, 255),
                  bg: tuple = (0, 0, 0), orientation: str = "landscape") -> None:
        self.ensure_info()
        w, h = logical_canvas_size(*self.size, orientation)
        self.send_image(render_text_image(text, w, h, font_size, fg, bg), orientation=orientation)

    # ── Playback (one at a time) ──────────────────────────────

    @property
    def playing(self) -> bool:
        return bool(self._play_thread and self._play_thread.is_alive())

    def start_playback(self, target: Callable[[threading.Event], None], name: str) -> threading.Event:
        """
        Stop whatever is playing, then run target(stop_event) on a background
        thread. target should return promptly once stop_event is set. Returns
        that event: it gets set when this playback is stopped or replaced.
        """
        with self._play_lock:
            self._stop_playback_locked()
            self.ensure_info()
            stop = threading.Event()
            self._play_stop = stop
            self.last_error = None

            def run() -> None:
                try:
                    target(stop)
                except DeviceError as e:
                    self.last_error = str(e)
                    log.error("%s stopped: %s", name, e)
                except Exception as e:
                    self.last_error = str(e)
                    log.exception("%s crashed", name)

            self._play_thread = threading.Thread(target=run, daemon=True, name=name)
            self._play_thread.start()
            return stop

    def stop_playback(self) -> None:
        with self._play_lock:
            self._stop_playback_locked()

    def _stop_playback_locked(self) -> None:
        self._play_stop.set()
        t = self._play_thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)
            if t.is_alive():
                log.warning("Playback thread %s did not stop within 3s", t.name)
        self._play_thread = None

    stop_gif = stop_playback   # older name, still used by GUI pages

    def play_frames(self, frames: Iterable[Frame], rotation: int = 0,
                    orientation: str = "landscape", name: str = "lcd-playback") -> None:
        """Play (image, duration_ms) pairs in real time on the playback thread."""

        def run(stop: threading.Event) -> None:
            pace_frames(frames, stop, lambda img: self.send_image(img, rotation, orientation))

        self.start_playback(run, name)

    def play_gif(self, path: str | Path, loop: bool = True, rotation: int = 0,
                 orientation: str = "landscape") -> None:
        self.play_frames(cycle_frames(load_gif_frames(path), loop), rotation, orientation, "gif-player")
        log.info("GIF playback started: %s", path)

    def play_video(self, path: str | Path, loop: bool = True, rotation: int = 0,
                   orientation: str = "landscape") -> None:
        """Stream a video (decoded on the fly, downscaled to the panel)."""
        frames = iter_video_frames(path, max_side=max(self.size), loop=loop)
        self.play_frames(frames, rotation, orientation, "video-player")
        log.info("Video playback started: %s", path)


def pace_frames(frames: Iterable[Frame], stop: threading.Event,
                show: Callable[[Image.Image], None], clock: Callable[[], float] = time.monotonic) -> int:
    """
    Show frames on a fixed schedule set by their durations. If sending
    falls behind (slow encode or link), frames whose time has already
    passed are skipped rather than burst out late, so playback keeps real
    time. Returns how many frames were shown.
    """
    shown = 0
    due = clock()
    for img, duration_ms in frames:
        if stop.is_set():
            break
        step = max(duration_ms, 1) / 1000.0
        if clock() > due + step:   # this frame's slot is already over: skip it
            due += step
            continue
        show(img)
        shown += 1
        due += step
        delay = due - clock()
        if delay > 0 and stop.wait(delay):
            break
    return shown
