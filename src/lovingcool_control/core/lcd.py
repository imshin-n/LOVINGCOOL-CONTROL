"""
lovingcool-control :: core/lcd.py
=========================
Image, GIF, and text -> USB frame pipeline.

Protocol confirmed against real hardware (33C3:7788, "txm818-7inch",
firmware "Ver1.0") by extracting the vendor's own Electron app source
(see README). This device's LCD channel expects JPEG-encoded frames,
not raw pixel data:

  1. Once per connection: send command key=0x11 ("commit"/start-live)
     over the command channel (AIODevice.send_command).
  2. For each frame: resize to the panel's native resolution, JPEG-encode
     at the highest quality that fits under a size cap (firmware-version
     dependent, ~50KB by default), and write the raw JPEG bytes straight
     to the serial port — no chunk headers, no per-chunk framing.
     Firmware versions > 2.8 additionally wrap the JPEG in a
     [4-byte LE length][jpeg][2-byte LE checksum] envelope; "Ver1.0"
     (this unit) does not use that wrapper.
"""

from __future__ import annotations

import io
import logging
import re
import threading
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageSequence
except ImportError as e:
    raise ImportError("Install Pillow: pip install Pillow") from e

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

from .device import AIODevice

MAX_VIDEO_FRAMES = 900   # ~30s at 30fps — cap to avoid unbounded memory use

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
#  Color Format Encoding
# ──────────────────────────────────────────────────────────────

def image_to_rgb565(img: Image.Image) -> bytes:
    """Convert a PIL Image (RGB) to raw RGB565 little-endian bytes."""
    img = img.convert("RGB")
    pixels = list(img.getdata())
    out = bytearray(len(pixels) * 2)
    for i, (r, g, b) in enumerate(pixels):
        r5 = (r >> 3) & 0x1F
        g6 = (g >> 2) & 0x3F
        b5 = (b >> 3) & 0x1F
        rgb565 = (r5 << 11) | (g6 << 5) | b5
        out[i*2]   = rgb565 & 0xFF
        out[i*2+1] = (rgb565 >> 8) & 0xFF
    return bytes(out)


def image_to_rgb888(img: Image.Image) -> bytes:
    """Convert a PIL Image to raw RGB888 bytes."""
    return img.convert("RGB").tobytes()


def image_to_bgr565(img: Image.Image) -> bytes:
    """Convert to BGR565 (some controllers use reversed byte order)."""
    img = img.convert("RGB")
    pixels = list(img.getdata())
    out = bytearray(len(pixels) * 2)
    for i, (r, g, b) in enumerate(pixels):
        b5 = (b >> 3) & 0x1F
        g6 = (g >> 2) & 0x3F
        r5 = (r >> 3) & 0x1F
        bgr565 = (b5 << 11) | (g6 << 5) | r5
        out[i*2]   = bgr565 & 0xFF
        out[i*2+1] = (bgr565 >> 8) & 0xFF
    return bytes(out)


FORMAT_ENCODERS = {
    "RGB565": image_to_rgb565,
    "BGR565": image_to_bgr565,
    "RGB888": image_to_rgb888,
}


def encode_jpeg_capped(img: Image.Image, max_size_kb: float, quality: int = 100) -> bytes:
    """
    JPEG-encode an image, stepping quality down until it fits under
    max_size_kb. Mirrors the vendor app's getSizeBt(): quality -= 2 per
    retry, floor of 1.
    """
    img = img.convert("RGB")
    while True:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) / 1024 <= max_size_kb or quality <= 1:
            return data
        quality = max(1, quality - 2)


# ──────────────────────────────────────────────────────────────
#  Image Processing
# ──────────────────────────────────────────────────────────────

def prepare_image(
    img: Image.Image,
    width: int,
    height: int,
    color_format: str = "RGB565",
    rotation: int = 0,
) -> bytes:
    """
    Resize, rotate and encode an image to the device's pixel format.
    Returns raw bytes ready for USB transmission.
    """
    img = img.convert("RGB")

    if rotation:
        img = img.rotate(-rotation, expand=False)

    img = img.resize((width, height), Image.LANCZOS)

    encoder = FORMAT_ENCODERS.get(color_format, image_to_rgb565)
    return encoder(img)


def load_image(path: str | Path) -> Image.Image:
    return Image.open(path)


def load_gif_frames(path: str | Path) -> list[tuple[Image.Image, int]]:
    """
    Returns list of (frame, duration_ms) tuples.
    """
    gif = Image.open(path)
    frames = []
    for frame in ImageSequence.Iterator(gif):
        duration = frame.info.get("duration", 100)  # ms
        frames.append((frame.copy(), duration))
    return frames


def load_video_frames(path: str | Path) -> list[tuple[Image.Image, int]]:
    """Decode a video file to (frame, duration_ms) pairs, RGB, capped at MAX_VIDEO_FRAMES."""
    if not CV2_AVAILABLE:
        raise RuntimeError("Video playback needs opencv-python-headless: pip install opencv-python-headless")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    duration_ms = max(1, int(1000 / fps))
    frames: list[tuple[Image.Image, int]] = []
    try:
        while len(frames) < MAX_VIDEO_FRAMES:
            ok, frame_bgr = cap.read()
            if not ok:
                break
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            frames.append((Image.fromarray(frame_rgb), duration_ms))
    finally:
        cap.release()
    if not frames:
        raise RuntimeError(f"No frames decoded from video: {path}")
    log.info("Loaded %d video frames from %s", len(frames), path)
    return frames


def load_video_thumbnail(path: str | Path) -> Image.Image:
    """Grab just the first frame of a video — for a lightweight preview thumbnail."""
    if not CV2_AVAILABLE:
        raise RuntimeError("Video preview needs opencv-python-headless: pip install opencv-python-headless")
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video: {path}")
        ok, frame_bgr = cap.read()
        if not ok:
            raise RuntimeError(f"Could not read a frame from video: {path}")
        return Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()


VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".webm")


def render_text_image(
    text: str,
    width: int,
    height: int,
    font_size: int = 40,
    fg_color: tuple = (255, 255, 255),
    bg_color: tuple = (0, 0, 0),
    font_path: str | None = None,
) -> Image.Image:
    """Render text onto a blank image (for LCD display)."""
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    try:
        if font_path:
            font = ImageFont.truetype(font_path, font_size)
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # Center the text
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (width - tw) // 2
    y = (height - th) // 2
    draw.text((x, y), text, fill=fg_color, font=font)
    return img


# ──────────────────────────────────────────────────────────────
#  Orientation (landscape/portrait) — shared with core/canvas.py
# ──────────────────────────────────────────────────────────────

def logical_canvas_size(device_width: int, device_height: int, orientation: str) -> tuple[int, int]:
    """
    The LCD panel's raster is a fixed physical shape (device_width x
    device_height). "Portrait" doesn't change that raster — it changes
    which aspect ratio content is composed/edited at, so a tall layout
    reads correctly once the whole frame is rotated 90 degrees into the
    panel's native buffer at send time (see orient_for_device()). This is
    the size any preview/editor should use, and what widget or text
    positions are relative to.
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
    """
    Take a frame composed at the logical (already-oriented) size and
    produce the final image at the device's fixed native raster size,
    ready to send. In portrait mode the logical canvas is already
    device_height x device_width, so a 90-degree expand rotate swaps it
    right back to device_width x device_height.
    """
    if orientation == "portrait":
        rotated = composed.rotate(90, resample=Image.BICUBIC, expand=True, fillcolor=fill_color)
        if rotated.size != (device_width, device_height):
            rotated = rotated.resize((device_width, device_height), Image.LANCZOS)
        return rotated
    if composed.size != (device_width, device_height):
        composed = composed.resize((device_width, device_height), Image.LANCZOS)
    return composed


# ──────────────────────────────────────────────────────────────
#  USB Frame Transmission
# ──────────────────────────────────────────────────────────────

class LCDController:
    """
    Manages sending frames to the LCD.

    Two device families, both confirmed against the vendor's own app source:

    - lcd_color_format == "JPEG" (this project's confirmed unit, and any
      33C3:7788-family device): each frame is a JPEG blob written directly
      to the serial port, after one "commit" command (cmd_commit) sent
      once per connection. No chunk headers.
    - Anything else: legacy raw-pixel path (RGB565/BGR565/RGB888),
      chunked over HID-style fixed-size packets — unverified guesswork,
      kept for HID-transport devices that don't match the JPEG family.
    """

    DEFAULT_MAX_JPEG_KB = 50   # matches vendor default for version <= 2.8

    def __init__(self, device: AIODevice):
        self.device = device
        self._gif_thread: threading.Thread | None = None
        self._stop_gif = threading.Event()
        self._color_format = device.spec.lcd_color_format
        self._width = device.spec.lcd_width
        self._height = device.spec.lcd_height
        self._live_started = False
        self._use_frame_wrapper = False   # set by refresh_info() if fw version > 2.8
        self._max_jpeg_kb = self.DEFAULT_MAX_JPEG_KB

    # ── Device info / live-session setup ──────────────────────

    def refresh_info(self) -> dict:
        """Query the device and update width/height/version-dependent behavior."""
        info = self.device.get_info()
        if info:
            self._width = info.get("width", self._width)
            self._height = info.get("height", self._height)
            version = str(info.get("version", ""))
            match = re.search(r"[\d.]+", version)
            version_num = float(match.group()) if match else None
            self._use_frame_wrapper = version_num is not None and version_num > 2.8
        return info

    def _ensure_live(self) -> None:
        """Send the one-time 'commit'/start-live command before streaming frames."""
        if not self._live_started:
            self.device.send_command(self.device.spec.cmd_commit, expect_response=False)
            self._live_started = True

    # ── JPEG frame transmission (confirmed protocol) ──────────

    def _build_jpeg_frame(self, jpeg_bytes: bytes) -> bytes:
        if not self._use_frame_wrapper:
            return jpeg_bytes
        length = len(jpeg_bytes)
        header = bytes([length & 0xFF, (length >> 8) & 0xFF, (length >> 16) & 0xFF, (length >> 24) & 0xFF])
        body = header + jpeg_bytes
        checksum = sum(body) & 0xFFFF
        return body + bytes([checksum & 0xFF, (checksum >> 8) & 0xFF])

    def send_raw_jpeg_frame(self, jpeg_bytes: bytes) -> None:
        """Send one already-encoded JPEG frame to the LCD."""
        self._ensure_live()
        self.device.send_packet(self._build_jpeg_frame(jpeg_bytes))

    def stop(self) -> None:
        """Stop live streaming. Resets state so the next send_image() re-commits."""
        if self._use_frame_wrapper:
            # Vendor app resets the device's image stream with a double
            # JPEG-EOI sentinel before other operations once live has run.
            self.device.send_packet(bytes([0xFF, 0xD9, 0xFF, 0xD9]))
        self._live_started = False

    # ── Legacy raw-pixel path (unverified, HID-style transports) ─

    def send_raw_frame(self, pixel_data: bytes) -> None:
        """Legacy chunked raw-pixel transmission — see class docstring."""
        spec = self.device.spec
        chunk_size = 60
        self.device.send_packet(bytes([spec.cmd_commit, 0x00] + [0x00] * 62))
        for seq, offset in enumerate(range(0, len(pixel_data), chunk_size)):
            chunk = pixel_data[offset:offset + chunk_size]
            header = bytes([spec.cmd_commit, (seq >> 8) & 0xFF, seq & 0xFF, 0x00])
            payload = header + chunk
            if len(payload) < 64:
                payload += bytes(64 - len(payload))
            self.device.send_packet(payload)
        log.debug("Raw frame sent: %d bytes", len(pixel_data))

    # ── Public API ────────────────────────────────────────────

    def send_image(self, img: Image.Image, rotation: int = 0, orientation: str = "landscape") -> None:
        """
        Encode and send a PIL image.

        orientation="landscape" (default) composes at the panel's native
        aspect. orientation="portrait" composes on a swapped-aspect
        canvas (so content isn't distorted/cropped) and rotates the whole
        frame 90 degrees into the panel's native buffer at the end —
        same approach as core.canvas's Screen Designer. `rotation` is a
        separate fine-tune applied on top of orientation, within the
        logical (already-oriented) canvas.
        """
        img = img.convert("RGB")
        logical_w, logical_h = logical_canvas_size(self._width, self._height, orientation)
        img = img.resize((logical_w, logical_h), Image.LANCZOS)
        if rotation % 360 != 0:
            img = img.rotate(-rotation, resample=Image.BICUBIC, expand=False)
        img = orient_for_device(img, self._width, self._height, orientation)

        if self._color_format == "JPEG":
            jpeg_bytes = encode_jpeg_capped(img, self._max_jpeg_kb)
            self.send_raw_jpeg_frame(jpeg_bytes)
        else:
            encoder = FORMAT_ENCODERS.get(self._color_format, image_to_rgb565)
            self.send_raw_frame(encoder(img))

    def send_image_file(self, path: str | Path, rotation: int = 0, orientation: str = "landscape") -> None:
        """Load an image file and send it."""
        self.send_image(load_image(path), rotation, orientation)

    def send_text(
        self,
        text: str,
        font_size: int = 40,
        fg: tuple = (255, 255, 255),
        bg: tuple = (0, 0, 0),
        orientation: str = "landscape",
    ) -> None:
        """Render text and send it to the LCD."""
        w, h = logical_canvas_size(self._width, self._height, orientation)
        img = render_text_image(text, w, h, font_size, fg, bg)
        self.send_image(img, orientation=orientation)

    # ── Animated playback (GIF or video) ──────────────────────

    def _play_frames(self, frames: list[tuple[Image.Image, int]], loop: bool,
                      rotation: int, orientation: str, thread_name: str) -> None:
        """Shared playback loop for play_gif()/play_video() — one frame at a time, on a background thread."""
        self.stop_gif()
        self._stop_gif.clear()

        def _play():
            while not self._stop_gif.is_set():
                for img, duration_ms in frames:
                    if self._stop_gif.is_set():
                        break
                    self.send_image(img, rotation, orientation)
                    self._stop_gif.wait(timeout=max(duration_ms, 1) / 1000.0)
                if not loop:
                    break

        self._gif_thread = threading.Thread(target=_play, daemon=True, name=thread_name)
        self._gif_thread.start()

    def play_gif(self, path: str | Path, loop: bool = True, rotation: int = 0,
                  orientation: str = "landscape") -> None:
        """Start playing a GIF in a background thread. Calls stop_gif() before starting a new one."""
        frames = load_gif_frames(path)
        self._play_frames(frames, loop, rotation, orientation, "gif-player")
        log.info("GIF playback started: %s", path)

    def play_video(self, path: str | Path, loop: bool = True, rotation: int = 0,
                    orientation: str = "landscape") -> None:
        """
        Start playing a video file (mp4/avi/mov/mkv/webm, via OpenCV) in a
        background thread. Frames are decoded up front (capped at
        MAX_VIDEO_FRAMES, ~30s), same pattern as play_gif().
        """
        frames = load_video_frames(path)
        self._play_frames(frames, loop, rotation, orientation, "video-player")
        log.info("Video playback started: %s", path)

    def stop_gif(self) -> None:
        """Stop GIF or video playback (whichever is running)."""
        self._stop_gif.set()
        if self._gif_thread and self._gif_thread.is_alive():
            self._gif_thread.join(timeout=2.0)
        self._gif_thread = None
